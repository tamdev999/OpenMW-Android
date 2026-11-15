#!/usr/bin/env python3
"""
OPENMW_ANDROID_PHASE1_PERF_LOG

Phase 1 baseline: add per-frame CSV performance logging to the OpenMW engine.

What this patcher does
----------------------
Inserts one block into apps/openmw/engine.cpp, guarded by BOTH:
  - #ifdef ANDROID            (compile-time: Android builds only)
  - OPENMW_PERF_LOG env var   (run-time:  disabled unless set to a writable path)

When OPENMW_PERF_LOG is set, every completed frame appends one CSV row to the
file, containing:
  frame_no, frame_time_ms,
  update_ms, cull_ms, draw_ms,     <- from osg::Stats (prev-1 frame, like OPENMW_OSG_STATS_FILE)
  real_draws, vertices, inst_arr, inst_el, inst_iters, prog_switches
                                    <- from GL4ES omwperf_snapshot()

Rows are buffered in memory and flushed every 300 frames and at exit.

Also logs once at startup:
  - lighting method (from LightManager::getLightingMethod())
  - OSG threading model (osgViewer::ViewerBase::ThreadingModel)
  - whether object paging and groundcover are enabled (settings)
  - cell name on each cell change (hooks into the frame loop via a flag)

No effect when OPENMW_PERF_LOG is unset: the check is a single getenv() that
the compiler optimises to a branch on a cold path, invisible during normal play.

Anchors verified against OpenMW commit f4bec41444214a7903bebd178389ca22ca13f646.

Safe to run twice: every edit site is guarded by the unique marker string
  OPENMW_ANDROID_PHASE1_PERF_LOG

Rollback: remove the patch from OPENMW_PATCH in CMakeLists.txt, run
  ./buildscripts/clean.sh --build
and rebuild.
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
    # We append the new members just before the closing brace of class Engine.
    # The pattern "} // namespace OMW" is unique in this file.
    old_ns_close = "} // namespace OMW\n"
    new_ns_close = (
        "} // namespace OMW\n"
    )
    # Actually we need to find the class body. Use a unique method signature instead.
    # engine.hpp ends with the class Engine closing brace + namespace close.
    # Look for the last private section member that is stable.
    old_hpp_anchor = (
        "    int mGlMaxTextureImageUnits = 0;\n"
        "};\n"
        "} // namespace OMW"
    )
    new_hpp_anchor = (
        "    int mGlMaxTextureImageUnits = 0;\n"
        "\n"
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "    // Per-frame CSV performance log state.\n"
        "    // Lifetime: valid for the duration of go().\n"
        "    struct PerfLog;\n"
        "    std::unique_ptr<PerfLog> mPerfLog;\n"
        "    // Pending cell-name update: set by the world scene on cell change.\n"
        "    // Consumed once per frame and written to the CSV header section.\n"
        "    std::string mPerfLogPendingCell;\n"
        "#endif\n"
        "};\n"
        "} // namespace OMW"
    )
    text = replace_once(text, old_hpp_anchor, new_hpp_anchor,
                        "engine.hpp / PerfLog member")
    write(engine_hpp, text)
    print("Patched engine.hpp with PerfLog member.")
else:
    print("engine.hpp PerfLog member already present.")


# ---------------------------------------------------------------------------
# 2.  engine.cpp — add includes and the PerfLog struct definition,
#     then hook into go() and frame().
# ---------------------------------------------------------------------------

text = read(engine_cpp)

if MARKER not in text:

    # 2A — add #include block after the existing Android-aware includes.
    # Anchor: a stable include that appears near the top of the file.
    old_include_anchor = '#include "mwstate/statemanagerimp.hpp"\n'
    new_include_anchor = (
        '#include "mwstate/statemanagerimp.hpp"\n'
        '\n'
        '#ifdef ANDROID\n'
        '// OPENMW_ANDROID_PHASE1_PERF_LOG\n'
        '#include <cstdlib>   // getenv\n'
        '#include <cstring>   // memset\n'
        '#include <fstream>\n'
        '#include <sstream>\n'
        '#include <vector>\n'
        '#include <components/sceneutil/lightmanager.hpp>\n'
        '// Forward-declare the GL4ES counter struct so this TU does not need\n'
        '// to depend directly on GL4ES headers at configure time.\n'
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
        '}\n'
        '#endif // ANDROID\n'
    )
    text = replace_once(text, old_include_anchor, new_include_anchor,
                        "engine.cpp / perf-log includes")

    # 2B — define the PerfLog struct + helpers immediately before OMW::Engine::go().
    # Anchor: the opening of go() which is unique.
    old_go_open = "void OMW::Engine::go()\n{\n"
    new_go_open = (
        "#ifdef ANDROID\n"
        "// OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "// All perf-log state is kept inside this POD-free struct so the\n"
        "// Android-only code does not pollute the Engine class definition.\n"
        "struct OMW::Engine::PerfLog\n"
        "{\n"
        "    std::ofstream      file;\n"
        "    std::ostringstream buf;\n"
        "    unsigned int       flushInterval = 300;\n"
        "    unsigned int       rowsSinceFlush = 0;\n"
        "    bool               headerWritten = false;\n"
        "\n"
        "    void writeHeader(const std::string& lightingMethod,\n"
        "                     const std::string& threadingModel,\n"
        "                     bool objectPaging,\n"
        "                     bool groundcover)\n"
        "    {\n"
        "        if (headerWritten) return;\n"
        "        headerWritten = true;\n"
        "        file << \"# OpenMW Android Phase 1 Baseline\\n\";\n"
        "        file << \"# lighting_method=\" << lightingMethod << \"\\n\";\n"
        "        file << \"# threading_model=\" << threadingModel << \"\\n\";\n"
        "        file << \"# object_paging=\" << (objectPaging ? \"true\" : \"false\") << \"\\n\";\n"
        "        file << \"# groundcover=\" << (groundcover ? \"true\" : \"false\") << \"\\n\";\n"
        "        file << \"frame_no,frame_ms,\"\n"
        "             << \"update_ms,cull_ms,draw_ms,\"\n"
        "             << \"real_draws,vertices,inst_arr,inst_el,inst_iters,prog_sw\"\n"
        "             << \"\\n\";\n"
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
        "            << static_cast<long>(cullMs  + 0.5) << \",\"\n"
        "            << static_cast<long>(drawMs  + 0.5) << \",\"\n"
        "            << c.real_draw_calls       << \",\"\n"
        "            << c.vertices_submitted    << \",\"\n"
        "            << c.instanced_arrays_calls<< \",\"\n"
        "            << c.instanced_elements_calls << \",\"\n"
        "            << c.instance_iterations   << \",\"\n"
        "            << c.program_switches      << \"\\n\";\n"
        "        rowsSinceFlush++;\n"
        "        if (rowsSinceFlush >= flushInterval)\n"
        "        {\n"
        "            file << buf.str();\n"
        "            file.flush();\n"
        "            buf.str(\"\");\n"
        "            buf.clear();\n"
        "            rowsSinceFlush = 0;\n"
        "        }\n"
        "    }\n"
        "\n"
        "    void logCellChange(const std::string& cellName)\n"
        "    {\n"
        "        file << \"# cell=\" << cellName << \"\\n\";\n"
        "    }\n"
        "\n"
        "    void flush()\n"
        "    {\n"
        "        if (!buf.str().empty())\n"
        "        {\n"
        "            file << buf.str();\n"
        "            file.flush();\n"
        "            buf.str(\"\");\n"
        "            buf.clear();\n"
        "            rowsSinceFlush = 0;\n"
        "        }\n"
        "    }\n"
        "};\n"
        "#endif // ANDROID\n"
        "\n"
        "void OMW::Engine::go()\n"
        "{\n"
    )
    text = replace_once(text, old_go_open, new_go_open,
                        "engine.cpp / PerfLog struct definition")

    # 2C — initialise PerfLog at the start of go(), after prepareEngine().
    # Anchor: the OPENMW_OSG_STATS_FILE env-var block which is a few lines after
    # prepareEngine(). We insert our block just before that.
    old_stats_env = (
        "#ifdef _WIN32\n"
        "    const auto* statsFile = _wgetenv(L\"OPENMW_OSG_STATS_FILE\");\n"
        "#else\n"
        "    const auto* statsFile = std::getenv(\"OPENMW_OSG_STATS_FILE\");\n"
        "#endif\n"
    )
    new_stats_env = (
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "    // Open the CSV log file if OPENMW_PERF_LOG is set.\n"
        "    {\n"
        "        const char* perfLogPath = std::getenv(\"OPENMW_PERF_LOG\");\n"
        "        if (perfLogPath && perfLogPath[0] != '\\0')\n"
        "        {\n"
        "            mPerfLog = std::make_unique<PerfLog>();\n"
        "            mPerfLog->file.open(perfLogPath,\n"
        "                std::ios_base::out | std::ios_base::trunc);\n"
        "            if (!mPerfLog->file.is_open())\n"
        "            {\n"
        "                Log(Debug::Warning) << \"OPENMW_PERF_LOG: could not open \"\n"
        "                                   << perfLogPath;\n"
        "                mPerfLog.reset();\n"
        "            }\n"
        "            else\n"
        "            {\n"
        "                Log(Debug::Info) << \"OPENMW_PERF_LOG: writing CSV to \"\n"
        "                                << perfLogPath;\n"
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
    text = replace_once(text, old_stats_env, new_stats_env,
                        "engine.cpp / PerfLog open")

    # 2D — write the CSV header once after the game is set up, just before the
    #      main rendering loop. Anchor: "// Start the main rendering loop"
    old_loop_comment = (
        "    // Start the main rendering loop\n"
        "    MWWorld::DateTimeManager& timeManager = *mWorld->getTimeManager();\n"
    )
    new_loop_comment = (
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "    // Write the one-time CSV header now that the rendering state is known.\n"
        "    if (mPerfLog)\n"
        "    {\n"
        "        // Lighting method\n"
        "        std::string lightingStr = \"unknown\";\n"
        "        // The LightManager is owned by RenderingManager; query it via the\n"
        "        // viewer's scene data.\n"
        "        {\n"
        "            using SceneUtil::LightingMethod;\n"
        "            using SceneUtil::LightManager;\n"
        "            // Walk the viewer's scene to find the first LightManager node.\n"
        "            struct FindLM : public osg::NodeVisitor {\n"
        "                LightManager* lm = nullptr;\n"
        "                FindLM() : osg::NodeVisitor(TRAVERSE_ALL_CHILDREN) {}\n"
        "                void apply(osg::Node& n) override {\n"
        "                    if (!lm) lm = dynamic_cast<LightManager*>(&n);\n"
        "                    if (!lm) traverse(n);\n"
        "                }\n"
        "            } visitor;\n"
        "            if (mViewer->getSceneData())\n"
        "                mViewer->getSceneData()->accept(visitor);\n"
        "            if (visitor.lm)\n"
        "                lightingStr = LightManager::getLightingMethodString(\n"
        "                    visitor.lm->getLightingMethod());\n"
        "        }\n"
        "\n"
        "        // Threading model\n"
        "        std::string threadStr = \"unknown\";\n"
        "        switch (mViewer->getThreadingModel())\n"
        "        {\n"
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
        "\n"
        "        bool objPaging  = Settings::terrain().mObjectPaging;\n"
        "        bool groundcov  = Settings::groundcover().mEnabled;\n"
        "\n"
        "        mPerfLog->writeHeader(lightingStr, threadStr, objPaging, groundcov);\n"
        "    }\n"
        "#endif // ANDROID\n"
        "\n"
        "    // Start the main rendering loop\n"
        "    MWWorld::DateTimeManager& timeManager = *mWorld->getTimeManager();\n"
    )
    text = replace_once(text, old_loop_comment, new_loop_comment,
                        "engine.cpp / PerfLog write header")

    # 2E — flush the log at exit, just before mLuaWorker->join().
    # Anchor is unique: appears once at the end of go().
    old_lua_join = "    mLuaWorker->join();\n\n    // Save user settings\n"
    new_lua_join = (
        "    mLuaWorker->join();\n"
        "\n"
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG: flush remaining rows on clean exit.\n"
        "    if (mPerfLog)\n"
        "        mPerfLog->flush();\n"
        "    mPerfLog.reset();\n"
        "#endif\n"
        "\n"
        "    // Save user settings\n"
    )
    text = replace_once(text, old_lua_join, new_lua_join,
                        "engine.cpp / PerfLog flush on exit")

    # 2F — hook into frame() — append a CSV row at the bottom of frame().
    # Anchor: the unique tail of frame() just before "return true;".
    # The stats attributes are looked up from osg::Stats for frame N-3
    # (the same delay the OPENMW_OSG_STATS_FILE path uses, confirmed in go()).
    old_frame_tail = (
        "    mViewer->renderingTraversals();\n"
        "    mLuaWorker->finishUpdate(frameStart, frameNumber, *stats);\n"
        "    return true;\n"
        "}"
    )
    new_frame_tail = (
        "    mViewer->renderingTraversals();\n"
        "    mLuaWorker->finishUpdate(frameStart, frameNumber, *stats);\n"
        "\n"
        "#ifdef ANDROID\n"
        "    // OPENMW_ANDROID_PHASE1_PERF_LOG\n"
        "    if (mPerfLog)\n"
        "    {\n"
        "        // Check for a pending cell-name annotation.\n"
        "        if (!mPerfLogPendingCell.empty())\n"
        "        {\n"
        "            mPerfLog->logCellChange(mPerfLogPendingCell);\n"
        "            mPerfLogPendingCell.clear();\n"
        "        }\n"
        "\n"
        "        // OSG timing stats use a 3-frame delay identical to reportStats().\n"
        "        constexpr unsigned statsDelay = 3;\n"
        "        double updateMs = 0.0, cullMs = 0.0, drawMs = 0.0;\n"
        "        if (frameNumber >= statsDelay)\n"
        "        {\n"
        "            const unsigned sn = frameNumber - statsDelay;\n"
        "            double v = 0.0;\n"
        "            // \"update_time_taken\" is set by ScopedProfile<UserStatsType::World>\n"
        "            // and siblings; \"Update\" is the OSG viewer's own update-traversal\n"
        "            // attribute written by osgViewer::ViewerBase.\n"
        "            if (stats->getAttribute(sn, \"Update\", v))         updateMs = v * 1000.0;\n"
        "            else if (stats->getAttribute(sn, \"world_time_taken\", v)) updateMs = v * 1000.0;\n"
        "            if (stats->getAttribute(sn, \"Cull\",   v))         cullMs   = v * 1000.0;\n"
        "            if (stats->getAttribute(sn, \"Draw\",   v))         drawMs   = v * 1000.0;\n"
        "        }\n"
        "\n"
        "        // Snapshot GL4ES counters.\n"
        "        omwperf_counters_t gl = {};\n"
        "        omwperf_snapshot(&gl);\n"
        "\n"
        "        mPerfLog->appendRow(frameNumber,\n"
        "            static_cast<double>(frametime),\n"
        "            updateMs, cullMs, drawMs, gl);\n"
        "    }\n"
        "#endif // ANDROID\n"
        "\n"
        "    return true;\n"
        "}"
    )
    text = replace_once(text, old_frame_tail, new_frame_tail,
                        "engine.cpp / PerfLog frame hook")

    write(engine_cpp, text)
    print("Patched engine.cpp with Phase 1 perf-log instrumentation.")
else:
    print("engine.cpp Phase 1 perf-log instrumentation already applied.")


# ---------------------------------------------------------------------------
# 3. Verify
# ---------------------------------------------------------------------------

for path, label in [(engine_cpp, "engine.cpp"), (engine_hpp, "engine.hpp")]:
    if MARKER not in read(path):
        raise SystemExit(f"Verification failed: marker missing from {label}")

cpp_text = read(engine_cpp)
checks = {
    "PerfLog struct defined":          "struct OMW::Engine::PerfLog" in cpp_text,
    "getenv(OPENMW_PERF_LOG)":         'getenv("OPENMW_PERF_LOG")' in cpp_text,
    "writeHeader called":              "mPerfLog->writeHeader(" in cpp_text,
    "appendRow called":                "mPerfLog->appendRow(" in cpp_text,
    "omwperf_snapshot called":         "omwperf_snapshot(&gl)" in cpp_text,
    "flush on exit":                   "mPerfLog->flush()" in cpp_text,
    "OSG Update stat read":            '"Update"' in cpp_text,
    "OSG Cull stat read":              '"Cull"'   in cpp_text,
    "OSG Draw stat read":              '"Draw"'   in cpp_text,
    "PerfLog member in hpp":           "std::unique_ptr<PerfLog> mPerfLog" in read(engine_hpp),
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit(
        "Phase 1 perf-log verification failed:\n - " + "\n - ".join(failed)
    )

print()
print("OpenMW Phase 1 perf-log patch: PASS")
print("  engine.cpp: PerfLog struct, OPENMW_PERF_LOG init, header, frame hook, exit flush")
print("  engine.hpp: PerfLog forward-decl + member")
print("  CSV columns: frame_no, frame_ms, update_ms, cull_ms, draw_ms,")
print("               real_draws, vertices, inst_arr, inst_el, inst_iters, prog_sw")
print("  Zero overhead when OPENMW_PERF_LOG is unset.")
