#!/usr/bin/env python3
"""
OPENMW_ANDROID_PHASE1_PERF_LOG

Phase 1 baseline: add per-frame CSV performance logging to the OpenMW engine.

What this patcher does
----------------------
Inserts one block into apps/openmw/engine.cpp, guarded by BOTH:
  - #ifdef ANDROID            (compile-time: Android builds only)
  - OPENMW_PERF_LOG env var   (run-time:  disabled unless set)

OPENMW_PERF_LOG may be set to either:
  - A file path   e.g. /sdcard/.../OpenMW/perf/run1.csv
  - A directory   e.g. /sdcard/.../OpenMW/perf/
    In that case the engine writes perf-<unix-timestamp>.csv inside it so
    repeated runs never overwrite each other.

On first frame the engine also writes a sidecar <csvpath>.env.txt containing:
  GL_VERSION, GL_RENDERER, the full GL_EXTENSIONS string, and the four queried
  extension flags, plus lighting method, threading model, paging/groundcover
  state.  This does NOT require logcat to be captured.

CSV columns (one row per frame):
  frame_no, frame_ms,
  update_ms, cull_ms, draw_ms,     <- from osg::Stats (3-frame delay)
  real_draws, vertices,
  inst_arr, inst_el, inst_iters,    <- GL4ES instancing counters
  prog_sw                           <- GL4ES program switches

Flushed every 300 frames AND at exit.

No effect when OPENMW_PERF_LOG is unset: the check is a single getenv() that
the compiler optimises to a never-taken branch, invisible during normal play.

Anchors verified against OpenMW commit f4bec41444214a7903bebd178389ca22ca13f646.

Safe to run twice: every edit site is guarded by the unique marker string
  OPENMW_ANDROID_PHASE1_PERF_LOG
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-android-perf-log.py <openmw-source-dir>")

root = Path(sys.argv[1])

engine_cpp = root / "apps" / "openmw" / "engine.cpp"
engine_hpp = root / "apps" / "openmw" / "engine.hpp"

for p in (engine_cpp, engine_hpp):
    if not p.is_file():
        raise SystemExit(f"missing expected OpenMW 0.51 source file: {p}")

MARKER = "OPENMW_ANDROID_PHASE1_PERF_LOG"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{label}: expected exactly one anchor, found {count}.\n"
            f"Anchor was:\n{old!r}"
        )
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# 1.  engine.hpp — add mPerfLog member fields (inside #ifdef ANDROID guard)
# ---------------------------------------------------------------------------

text = read(engine_hpp)

if MARKER not in text:
    # Anchor on the end of the file: the class-closing };, namespace }, and #endif.
    # This is unique and unaffected by other patchers.
    old_hpp = (
        "    };\n"
        "}\n"
        "\n"
        "#endif /* ENGINE_H */"
    )
    new_hpp = (
        "\n"
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "    struct PerfLog;\n"
        "    std::unique_ptr<PerfLog> mPerfLog;\n"
        "    std::string mPerfLogPendingCell;\n"
        "#endif\n"
        "    };\n"
        "}\n"
        "\n"
        "#endif /* ENGINE_H */"
    )
    text = replace_once(text, old_hpp, new_hpp, "engine.hpp / PerfLog member")
    write(engine_hpp, text)
    print("Patched engine.hpp.")
else:
    print("engine.hpp already patched.")


# ---------------------------------------------------------------------------
# 2.  engine.cpp
# ---------------------------------------------------------------------------

text = read(engine_cpp)

if MARKER not in text:

    # 2A — includes
    old_inc = '#include "mwstate/statemanagerimp.hpp"\n'
    new_inc = (
        '#include "mwstate/statemanagerimp.hpp"\n'
        '\n'
        '#ifdef ANDROID\n'
        '// OPENMW_ANDROID_PHASE1_PERF_LOG\n'
        '#include <cstdlib>\n'
        '#include <cstring>\n'
        '#include <ctime>\n'
        '#include <fstream>\n'
        '#include <sstream>\n'
        '#include <sys/stat.h>\n'
        '#include <components/sceneutil/lightmanager.hpp>\n'
        'extern "C" {\n'
        '    typedef struct {\n'
        '        unsigned int real_draw_calls;\n'
        '        unsigned int vertices_submitted;\n'
        '        unsigned int instanced_arrays_calls;\n'
        '        unsigned int instanced_elements_calls;\n'
        '        unsigned int instance_iterations;\n'
        '        unsigned int program_switches;\n'
        '        unsigned int ext_log_done;\n'
        '    } omwperf_counters_t;\n'
        '    void omwperf_snapshot(omwperf_counters_t* out);\n'
        '    int  omwperf_query_gl_env(const char** version, const char** renderer,\n'
        '                              const char** extensions);\n'
        '}\n'
        '#endif // ANDROID\n'
    )
    text = replace_once(text, old_inc, new_inc, "engine.cpp / perf-log includes")

    # 2B — PerfLog struct definition + go() opening
    old_go = "void OMW::Engine::go()\n{\n"
    new_go = (
        "#ifdef ANDROID\n"
        "// OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "struct OMW::Engine::PerfLog\n"
        "{\n"
        "    std::ofstream      file;\n"
        "    std::string        csvPath;      // actual path of the CSV\n"
        "    std::ostringstream buf;\n"
        "    unsigned int       flushInterval = 300;\n"
        "    unsigned int       rowsSinceFlush = 0;\n"
        "    bool               headerWritten = false;\n"
        "    bool               envWritten    = false;\n"
        "\n"
        "    // Write the sidecar .env.txt with GL and engine state.\n"
        "    // Does NOT depend on logcat; writes directly to disk.\n"
        "    void writeEnv(const std::string& lightingMethod,\n"
        "                  const std::string& threadingModel,\n"
        "                  bool objectPaging, bool groundcover)\n"
        "    {\n"
        "        if (envWritten || csvPath.empty()) return;\n"
        "        envWritten = true;\n"
        "        std::string envPath = csvPath + \".env.txt\";\n"
        "        std::ofstream ef(envPath, std::ios_base::out | std::ios_base::trunc);\n"
        "        if (!ef.is_open()) return;\n"
        "        const char *ver = nullptr, *rend = nullptr, *exts = nullptr;\n"
        "        omwperf_query_gl_env(&ver, &rend, &exts);\n"
        "        ef << \"GL_VERSION=\" << (ver  ? ver  : \"(null)\") << \"\\n\";\n"
        "        ef << \"GL_RENDERER=\" << (rend ? rend : \"(null)\") << \"\\n\";\n"
        "        ef << \"GL_EXTENSIONS=\" << (exts ? exts : \"(null)\") << \"\\n\";\n"
        "        if (exts) {\n"
        "            ef << \"GL_EXT_instanced_arrays=\"\n"
        "               << (strstr(exts,\"GL_EXT_instanced_arrays\")  ? 1 : 0) << \"\\n\";\n"
        "            ef << \"GL_EXT_draw_instanced=\"\n"
        "               << (strstr(exts,\"GL_EXT_draw_instanced\")     ? 1 : 0) << \"\\n\";\n"
        "            ef << \"GL_OES_vertex_array_object=\"\n"
        "               << (strstr(exts,\"GL_OES_vertex_array_object\") ? 1 : 0) << \"\\n\";\n"
        "            ef << \"GL_EXT_multi_draw_indirect=\"\n"
        "               << (strstr(exts,\"GL_EXT_multi_draw_indirect\") ? 1 : 0) << \"\\n\";\n"
        "        }\n"
        "        ef << \"lighting_method=\" << lightingMethod << \"\\n\";\n"
        "        ef << \"threading_model=\" << threadingModel << \"\\n\";\n"
        "        ef << \"object_paging=\" << (objectPaging ? \"true\" : \"false\") << \"\\n\";\n"
        "        ef << \"groundcover=\" << (groundcover ? \"true\" : \"false\") << \"\\n\";\n"
        "        ef.flush();\n"
        "    }\n"
        "\n"
        "    void writeHeader()\n"
        "    {\n"
        "        if (headerWritten) return;\n"
        "        headerWritten = true;\n"
        "        file << \"frame_no,frame_ms,\"\n"
        "             << \"update_ms,cull_ms,draw_ms,\"\n"
        "             << \"real_draws,vertices,\"\n"
        "             << \"inst_arr,inst_el,inst_iters,prog_sw\\n\";\n"
        "        file.flush();\n"
        "    }\n"
        "\n"
        "    void appendRow(unsigned int frameNo, double frameSec,\n"
        "                   double updateMs, double cullMs, double drawMs,\n"
        "                   const omwperf_counters_t& c)\n"
        "    {\n"
        "        buf << frameNo << \",\"\n"
        "            << static_cast<long>(frameSec * 1000.0 + 0.5) << \",\"\n"
        "            << static_cast<long>(updateMs + 0.5) << \",\"\n"
        "            << static_cast<long>(cullMs   + 0.5) << \",\"\n"
        "            << static_cast<long>(drawMs   + 0.5) << \",\"\n"
        "            << c.real_draw_calls            << \",\"\n"
        "            << c.vertices_submitted         << \",\"\n"
        "            << c.instanced_arrays_calls     << \",\"\n"
        "            << c.instanced_elements_calls   << \",\"\n"
        "            << c.instance_iterations        << \",\"\n"
        "            << c.program_switches           << \"\\n\";\n"
        "        rowsSinceFlush++;\n"
        "        if (rowsSinceFlush >= flushInterval)\n"
        "            flush();\n"
        "    }\n"
        "\n"
        "    void logCellChange(const std::string& cellName)\n"
        "    {\n"
        "        // Flush buffered rows first so the annotation appears at the right place.\n"
        "        flush();\n"
        "        file << \"# cell=\" << cellName << \"\\n\";\n"
        "        file.flush();\n"
        "    }\n"
        "\n"
        "    void flush()\n"
        "    {\n"
        "        if (buf.str().empty()) return;\n"
        "        file << buf.str();\n"
        "        file.flush();\n"
        "        buf.str(\"\");\n"
        "        buf.clear();\n"
        "        rowsSinceFlush = 0;\n"
        "    }\n"
        "};\n"
        "#endif // ANDROID\n"
        "\n"
        "void OMW::Engine::go()\n"
        "{\n"
    )
    text = replace_once(text, old_go, new_go, "engine.cpp / PerfLog struct")

    # 2C — open the log file (before the OPENMW_OSG_STATS_FILE block)
    old_stats_env = (
        "#ifdef _WIN32\n"
        "    const auto* statsFile = _wgetenv(L\"OPENMW_OSG_STATS_FILE\");\n"
        "#else\n"
        "    const auto* statsFile = std::getenv(\"OPENMW_OSG_STATS_FILE\");\n"
        "#endif\n"
    )
    new_stats_env = (
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG: resolve path and open CSV.\n"
        "    {\n"
        "        const char* envVal = std::getenv(\"OPENMW_PERF_LOG\");\n"
        "        if (envVal && envVal[0] != '\\0') {\n"
        "            std::string resolvedPath(envVal);\n"
        "            // If the path is a directory, generate a timestamped filename.\n"
        "            struct stat st;\n"
        "            if (stat(envVal, &st) == 0 && S_ISDIR(st.st_mode)) {\n"
        "                std::string dir(envVal);\n"
        "                if (dir.back() != '/') dir += '/';\n"
        "                resolvedPath = dir + \"perf-\" +\n"
        "                    std::to_string(static_cast<long>(std::time(nullptr))) + \".csv\";\n"
        "            }\n"
        "            mPerfLog = std::make_unique<PerfLog>();\n"
        "            mPerfLog->csvPath = resolvedPath;\n"
        "            mPerfLog->file.open(resolvedPath,\n"
        "                std::ios_base::out | std::ios_base::trunc);\n"
        "            if (!mPerfLog->file.is_open()) {\n"
        "                Log(Debug::Warning) << \"OPENMW_PERF_LOG: could not open \"\n"
        "                                   << resolvedPath;\n"
        "                mPerfLog.reset();\n"
        "            } else {\n"
        "                Log(Debug::Info) << \"OPENMW_PERF_LOG: writing CSV to \"\n"
        "                                << resolvedPath;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "#endif // ANDROID\n"
        "\n"
        "#ifdef _WIN32\n"
        "    const auto* statsFile = _wgetenv(L\"OPENMW_OSG_STATS_FILE\");\n"
        "#else\n"
        "    const auto* statsFile = std::getenv(\"OPENMW_OSG_STATS_FILE\");\n"
        "#endif\n"
    )
    text = replace_once(text, old_stats_env, new_stats_env, "engine.cpp / PerfLog open")

    # 2D — write CSV header + sidecar .env.txt before the main loop
    old_loop = (
        "    // Start the main rendering loop\n"
        "    MWWorld::DateTimeManager& timeManager = *mWorld->getTimeManager();\n"
    )
    new_loop = (
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG: write header and sidecar env file.\n"
        "    if (mPerfLog) {\n"
        "        // Lighting method via scene-graph walk.\n"
        "        std::string lightingStr = \"unknown\";\n"
        "        {\n"
        "            using SceneUtil::LightManager;\n"
        "            struct FindLM : public osg::NodeVisitor {\n"
        "                LightManager* lm = nullptr;\n"
        "                FindLM() : osg::NodeVisitor(TRAVERSE_ALL_CHILDREN) {}\n"
        "                void apply(osg::Node& n) override {\n"
        "                    if (!lm) lm = dynamic_cast<LightManager*>(&n);\n"
        "                    if (!lm) traverse(n);\n"
        "                }\n"
        "            } vis;\n"
        "            if (mViewer->getSceneData())\n"
        "                mViewer->getSceneData()->accept(vis);\n"
        "            if (vis.lm)\n"
        "                lightingStr = LightManager::getLightingMethodString(\n"
        "                    vis.lm->getLightingMethod());\n"
        "        }\n"
        "        // Threading model.\n"
        "        std::string threadStr = \"unknown\";\n"
        "        switch (mViewer->getThreadingModel()) {\n"
        "            case osgViewer::ViewerBase::SingleThreaded:\n"
        "                threadStr = \"SingleThreaded\"; break;\n"
        "            case osgViewer::ViewerBase::CullDrawThreadPerContext:\n"
        "                threadStr = \"CullDrawThreadPerContext\"; break;\n"
        "            case osgViewer::ViewerBase::DrawThreadPerContext:\n"
        "                threadStr = \"DrawThreadPerContext\"; break;\n"
        "            case osgViewer::ViewerBase::CullThreadPerCameraDrawThreadPerContext:\n"
        "                threadStr = \"CullThreadPerCameraDrawThreadPerContext\"; break;\n"
        "            default: break;\n"
        "        }\n"
        "        bool objPaging = Settings::terrain().mObjectPaging;\n"
        "        bool groundcov = Settings::groundcover().mEnabled;\n"
        "        // Write .env.txt sidecar (no logcat needed).\n"
        "        mPerfLog->writeEnv(lightingStr, threadStr, objPaging, groundcov);\n"
        "        // Write CSV header row.\n"
        "        mPerfLog->writeHeader();\n"
        "    }\n"
        "#endif // ANDROID\n"
        "\n"
        "    // Start the main rendering loop\n"
        "    MWWorld::DateTimeManager& timeManager = *mWorld->getTimeManager();\n"
    )
    text = replace_once(text, old_loop, new_loop, "engine.cpp / PerfLog header + env")

    # 2E — flush at exit
    # The runtime-baseline patcher inserted Android g_viewer.release() code between
    # mLuaWorker->join() and "// Save user settings". Anchor on the text that now
    # immediately precedes "// Save user settings".
    old_exit = (
        "#endif\n"
        "\n"
        "    // Save user settings\n"
    )
    new_exit = (
        "#endif\n"
        "\n"
        "#ifdef ANDROID\n"
        "    if (mPerfLog) { mPerfLog->flush(); }\n"
        "    mPerfLog.reset();\n"
        "#endif\n"
        "\n"
        "    // Save user settings\n"
    )
    text = replace_once(text, old_exit, new_exit, "engine.cpp / PerfLog flush on exit")

    # 2F — per-frame hook at the bottom of frame()
    old_frame = (
        "    mViewer->renderingTraversals();\n"
        "\n"
        "    mLuaWorker->finishUpdate(frameStart, frameNumber, *stats);\n"
        "\n"
        "    return true;\n"
        "}"
    )
    new_frame = (
        "    mViewer->renderingTraversals();\n"
        "\n"
        "    mLuaWorker->finishUpdate(frameStart, frameNumber, *stats);\n"
        "\n"
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "    if (mPerfLog) {\n"
        "        if (!mPerfLogPendingCell.empty()) {\n"
        "            mPerfLog->logCellChange(mPerfLogPendingCell);\n"
        "            mPerfLogPendingCell.clear();\n"
        "        }\n"
        "        // OSG stats use a 3-frame delay (same as reportStats() in go()).\n"
        "        constexpr unsigned statsDelay = 3;\n"
        "        double updateMs = 0.0, cullMs = 0.0, drawMs = 0.0;\n"
        "        if (frameNumber >= statsDelay) {\n"
        "            const unsigned sn = frameNumber - statsDelay;\n"
        "            double v = 0.0;\n"
        "            if (stats->getAttribute(sn, \"Update\", v))              updateMs = v * 1000.0;\n"
        "            else if (stats->getAttribute(sn, \"world_time_taken\", v)) updateMs = v * 1000.0;\n"
        "            if (stats->getAttribute(sn, \"Cull\", v))  cullMs = v * 1000.0;\n"
        "            if (stats->getAttribute(sn, \"Draw\", v))  drawMs = v * 1000.0;\n"
        "        }\n"
        "        omwperf_counters_t gl = {};\n"
        "        omwperf_snapshot(&gl);\n"
        "        mPerfLog->appendRow(frameNumber, static_cast<double>(frametime),\n"
        "                            updateMs, cullMs, drawMs, gl);\n"
        "    }\n"
        "#endif // ANDROID\n"
        "\n"
        "    return true;\n"
        "}"
    )
    text = replace_once(text, old_frame, new_frame, "engine.cpp / PerfLog frame hook")

    write(engine_cpp, text)
    print("Patched engine.cpp.")
else:
    print("engine.cpp already patched.")


# ---------------------------------------------------------------------------
# 3. Verify
# ---------------------------------------------------------------------------

for path, label in [(engine_cpp, "engine.cpp"), (engine_hpp, "engine.hpp")]:
    if MARKER not in read(path):
        raise SystemExit(f"Verification failed: marker missing from {label}")

cpp = read(engine_cpp)
hpp = read(engine_hpp)
checks = {
    "PerfLog struct":                "struct OMW::Engine::PerfLog" in cpp,
    "getenv OPENMW_PERF_LOG":        'getenv("OPENMW_PERF_LOG")' in cpp,
    "directory stat check":          "S_ISDIR(st.st_mode)" in cpp,
    "timestamped filename":          'std::to_string(static_cast<long>(std::time(nullptr)))' in cpp,
    "csvPath member":                "mPerfLog->csvPath" in cpp,
    "writeEnv called":               "mPerfLog->writeEnv(" in cpp,
    "writeHeader called":            "mPerfLog->writeHeader()" in cpp,
    "appendRow called":              "mPerfLog->appendRow(" in cpp,
    "omwperf_query_gl_env declared": "omwperf_query_gl_env" in cpp,
    "omwperf_snapshot called":       "omwperf_snapshot(&gl)" in cpp,
    "flush every 300 rows":          "rowsSinceFlush >= flushInterval" in cpp,
    "flush on exit":                 "mPerfLog->flush()" in cpp,
    "OSG Update stat":               '"Update"' in cpp,
    "OSG Cull stat":                 '"Cull"'   in cpp,
    "OSG Draw stat":                 '"Draw"'   in cpp,
    "env.txt written":               '.env.txt\"' in cpp,
    "GL_EXTENSIONS in env.txt":      '"GL_EXTENSIONS="' in cpp,
    "PerfLog member in hpp":         "std::unique_ptr<PerfLog> mPerfLog" in hpp,
}
failed = [k for k, v in checks.items() if not v]
if failed:
    raise SystemExit("Perf-log verification failed:\n - " + "\n - ".join(failed))

print()
print("OpenMW Phase 1 perf-log patch: PASS")
print("  CSV: frame_no,frame_ms,update_ms,cull_ms,draw_ms,real_draws,vertices,")
print("       inst_arr,inst_el,inst_iters,prog_sw")
print("  Sidecar .env.txt: GL_VERSION, GL_RENDERER, GL_EXTENSIONS + 4 ext flags,")
print("                    lighting_method, threading_model, object_paging, groundcover")
print("  OPENMW_PERF_LOG=<file>  → write to that file")
print("  OPENMW_PERF_LOG=<dir>/  → write perf-<timestamp>.csv inside dir")
print("  Flush every 300 rows + on exit")
print("  Zero overhead when OPENMW_PERF_LOG is unset")
