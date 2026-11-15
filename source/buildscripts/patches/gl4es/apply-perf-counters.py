#!/usr/bin/env python3
"""
OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS

Phase 1 baseline: add per-frame draw-call counters to GL4ES.

Instrumented sites (all in src/gl/drawing.c and src/gl/fpe.c):

  drawing.c  glDrawElementsCommon()  — single funnel for every real GLES draw
    • gles_glDrawArrays  path  (instancecount==1 || hardext.esversion==1)
    • gles_glDrawElements path (instancecount==1 || hardext.esversion==1)
    • fpe_glDrawArraysInstanced   call (instancecount>1, esversion>1)
    • fpe_glDrawElementsInstanced call (instancecount>1, esversion>1)

  fpe.c  fpe_glDrawArraysInstanced()   — per-instance gles_glDrawArrays loop
  fpe.c  fpe_glDrawElementsInstanced() — per-instance gles_glDrawElements loop

  drawing.c  glDrawElementsCommon()   — vertex/index count accumulation

  program.c  gl4es_glUseProgram()     — program switch counter

Counter struct (omwperf_counters_t) is declared in a new header
  src/gl/omwperf.h
and defined once in
  src/gl/omwperf.c

The exported C function:
  void omwperf_snapshot(omwperf_counters_t *out);
copies the live counters into *out and zeroes them atomically enough for a
single-threaded render loop (no locking — plain increments, as specified).

Safe to run twice: every edit site is guarded by the unique marker string
  OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS

Rollback: remove the two new files and revert the edits in drawing.c, fpe.c,
program.c by running ./buildscripts/clean.sh --build and rebuilding.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-perf-counters.py <gl4es-source-dir>")

root = Path(sys.argv[1])

drawing_c   = root / "src" / "gl" / "drawing.c"
fpe_c       = root / "src" / "gl" / "fpe.c"
program_c   = root / "src" / "gl" / "program.c"
omwperf_h   = root / "src" / "gl" / "omwperf.h"
omwperf_c   = root / "src" / "gl" / "omwperf.c"
android_mk  = root / "Android.mk"

for p in (drawing_c, fpe_c, program_c, android_mk):
    if not p.is_file():
        raise SystemExit(f"missing expected gl4es source file: {p}")

MARKER = "OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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
# 1. Write src/gl/omwperf.h  (new file — always overwrite to keep idempotent)
# ---------------------------------------------------------------------------

omwperf_h_text = r"""#ifndef OMWPERF_H
#define OMWPERF_H
/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS
 * Per-frame GL4ES draw-call counters for the OpenMW Android Phase 1 baseline.
 * All fields are plain unsigned 32-bit counters incremented without locks.
 * The render loop is single-threaded; no synchronisation is needed.
 */
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    /* Real GLES draw calls that reached the driver */
    unsigned int real_draw_calls;
    /* Total vertex / index count submitted across all real draws */
    unsigned int vertices_submitted;
    /* Number of times fpe_glDrawArraysInstanced was called */
    unsigned int instanced_arrays_calls;
    /* Number of times fpe_glDrawElementsInstanced was called */
    unsigned int instanced_elements_calls;
    /* Total emulated instance iterations inside the two instanced loops */
    unsigned int instance_iterations;
    /* gl4es-level glUseProgram switches (program id changes) */
    unsigned int program_switches;
    /* One-time extension log has been emitted */
    unsigned int ext_log_done;
} omwperf_counters_t;

/* Live counter set — incremented in the hot path */
extern omwperf_counters_t omwperf_live;

/* Copy live counters into *out and reset them to zero.
 * Call once per frame from the OpenMW perf-log patch. */
void omwperf_snapshot(omwperf_counters_t *out);

/* Emit GL_VERSION / GL_RENDERER / GL_EXTENSIONS to Android logcat once.
 * Called the first time omwperf_snapshot() is invoked. */
void omwperf_log_gl_env_once(void);

#ifdef __cplusplus
}
#endif
#endif /* OMWPERF_H */
"""

write(omwperf_h, omwperf_h_text)
print("Written src/gl/omwperf.h")


# ---------------------------------------------------------------------------
# 2. Write src/gl/omwperf.c  (new file — always overwrite to keep idempotent)
# ---------------------------------------------------------------------------

omwperf_c_text = r"""/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS
 * Phase 1 baseline: counter definition and snapshot function.
 */
#include "omwperf.h"
#include <string.h>

#ifdef USE_ANDROID_LOG
#include <android/log.h>
#define OMWPERF_TAG "OpenMW-GL4ES-Perf"
#define OMWPERF_LOG(...) __android_log_print(ANDROID_LOG_INFO, OMWPERF_TAG, __VA_ARGS__)
#else
#include <stdio.h>
#define OMWPERF_LOG(...) fprintf(stderr, __VA_ARGS__)
#endif

/* Include the GLES loader so we can call glGetString at runtime. */
#include "gles.h"
#include "loader.h"

omwperf_counters_t omwperf_live = {0};

void omwperf_snapshot(omwperf_counters_t *out) {
    omwperf_log_gl_env_once();
    *out = omwperf_live;
    memset(&omwperf_live, 0, sizeof(omwperf_live));
    /* Preserve the one-shot flag across resets. */
    omwperf_live.ext_log_done = out->ext_log_done;
}

void omwperf_log_gl_env_once(void) {
    if (omwperf_live.ext_log_done)
        return;
    omwperf_live.ext_log_done = 1;

    LOAD_GLES(glGetString);
    if (!gles_glGetString) {
        OMWPERF_LOG("omwperf: glGetString not available");
        return;
    }

    const char *version  = (const char *)gles_glGetString(GL_VERSION);
    const char *renderer = (const char *)gles_glGetString(GL_RENDERER);
    const char *exts     = (const char *)gles_glGetString(GL_EXTENSIONS);

    OMWPERF_LOG("GL_VERSION:  %s",  version  ? version  : "(null)");
    OMWPERF_LOG("GL_RENDERER: %s",  renderer ? renderer : "(null)");

    /* Log extensions in 512-char slices so logcat does not truncate them. */
    if (exts) {
        const char *p = exts;
        char buf[513];
        int  chunk = 512;
        while (*p) {
            int len = 0;
            while (p[len] && len < chunk) ++len;
            /* Don't split in the middle of an extension name */
            if (p[len] && len == chunk) {
                while (len > 0 && p[len-1] != ' ') --len;
                if (len == 0) len = chunk; /* degenerate: no space found */
            }
            memcpy(buf, p, (size_t)len);
            buf[len] = '\0';
            OMWPERF_LOG("GL_EXTENSIONS chunk: %s", buf);
            p += len;
            while (*p == ' ') ++p;
        }
    } else {
        OMWPERF_LOG("GL_EXTENSIONS: (null)");
    }

    /* Log the specific extensions the prompt asks about */
    int has_inst_arrays   = exts && (strstr(exts, "GL_EXT_instanced_arrays") != 0);
    int has_draw_inst     = exts && (strstr(exts, "GL_EXT_draw_instanced") != 0);
    int has_vao           = exts && (strstr(exts, "GL_OES_vertex_array_object") != 0);
    int has_mdi           = exts && (strstr(exts, "GL_EXT_multi_draw_indirect") != 0);
    OMWPERF_LOG("GL_EXT_instanced_arrays=%d GL_EXT_draw_instanced=%d "
                "GL_OES_vertex_array_object=%d GL_EXT_multi_draw_indirect=%d",
                has_inst_arrays, has_draw_inst, has_vao, has_mdi);
}
"""

write(omwperf_c, omwperf_c_text)
print("Written src/gl/omwperf.c")


# ---------------------------------------------------------------------------
# 3. Patch drawing.c — glDrawElementsCommon()
#    Three insertion points:
#    A. Count real draws + vertices for the gles_glDrawArrays / gles_glDrawElements
#       direct path (instancecount==1 || esversion==1).
#    B. Count the instanced dispatch calls (fpe_glDrawArraysInstanced /
#       fpe_glDrawElementsInstanced).
#    C. Add #include "omwperf.h" near the top.
# ---------------------------------------------------------------------------

text = read(drawing_c)

if MARKER not in text:
    # 3A — add include
    text = replace_once(
        text,
        '#include "../glx/hardext.h"\n',
        '#include "../glx/hardext.h"\n'
        '/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n'
        '#include "omwperf.h"\n',
        "drawing.c / omwperf include",
    )

    # 3B — real draw counters: direct path
    # Anchor: the two gles calls under "if(instancecount==1 || hardext.esversion==1)"
    old_direct = (
        "        if(instancecount==1 || hardext.esversion==1) {\n"
        "            if(!iindices && !sindices)\n"
        "                gles_glDrawArrays(mode, first, count);\n"
        "            else\n"
        "                gles_glDrawElements(mode, count, (sindices)?GL_UNSIGNED_SHORT:GL_UNSIGNED_INT, (sindices?((void*)sindices):((void*)iindices)));\n"
        "        } else {"
    )
    new_direct = (
        "        if(instancecount==1 || hardext.esversion==1) {\n"
        "            /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS: real draw + vertex count */\n"
        "            omwperf_live.real_draw_calls++;\n"
        "            omwperf_live.vertices_submitted += (unsigned int)count;\n"
        "            if(!iindices && !sindices)\n"
        "                gles_glDrawArrays(mode, first, count);\n"
        "            else\n"
        "                gles_glDrawElements(mode, count, (sindices)?GL_UNSIGNED_SHORT:GL_UNSIGNED_INT, (sindices?((void*)sindices):((void*)iindices)));\n"
        "        } else {"
    )
    text = replace_once(text, old_direct, new_direct, "drawing.c / direct draw counters")

    # 3C — instanced dispatch counters
    old_instanced = (
        "        } else {\n"
        "            if(!iindices && !sindices)\n"
        "                fpe_glDrawArraysInstanced(mode, first, count,instancecount);\n"
        "            else {\n"
        "                void* tmp=(sindices?((void*)sindices):((void*)iindices));\n"
        "                GLenum t = (sindices)?GL_UNSIGNED_SHORT:GL_UNSIGNED_INT;\n"
        "                fpe_glDrawElementsInstanced(mode, count, t, tmp, instancecount);\n"
        "            }\n"
        "        }"
    )
    new_instanced = (
        "        } else {\n"
        "            /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS: instanced dispatch */\n"
        "            if(!iindices && !sindices) {\n"
        "                omwperf_live.instanced_arrays_calls++;\n"
        "                omwperf_live.vertices_submitted += (unsigned int)(count * instancecount);\n"
        "                fpe_glDrawArraysInstanced(mode, first, count,instancecount);\n"
        "            } else {\n"
        "                omwperf_live.instanced_elements_calls++;\n"
        "                omwperf_live.vertices_submitted += (unsigned int)(count * instancecount);\n"
        "                void* tmp=(sindices?((void*)sindices):((void*)iindices));\n"
        "                GLenum t = (sindices)?GL_UNSIGNED_SHORT:GL_UNSIGNED_INT;\n"
        "                fpe_glDrawElementsInstanced(mode, count, t, tmp, instancecount);\n"
        "            }\n"
        "        }"
    )
    text = replace_once(text, old_instanced, new_instanced, "drawing.c / instanced dispatch counters")

    write(drawing_c, text)
    print("Patched src/gl/drawing.c with perf counters.")
else:
    print("src/gl/drawing.c perf counters already applied.")


# ---------------------------------------------------------------------------
# 4. Patch fpe.c — count per-instance iterations inside the two loops
# ---------------------------------------------------------------------------

text = read(fpe_c)

if MARKER not in text:
    # 4A — fpe_glDrawArraysInstanced: add include and iteration counter
    text = replace_once(
        text,
        '#include "../glx/hardext.h"\n',
        '#include "../glx/hardext.h"\n'
        '/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n'
        '#include "omwperf.h"\n',
        "fpe.c / omwperf include",
    )

    # Anchor: the gles_glDrawArrays call at the bottom of the per-instance loop.
    # This is inside "for (GLint id=0; id<primcount; ++id)" in fpe_glDrawArraysInstanced.
    old_inst_arr_call = (
        "        gles_glDrawArrays(mode, first, count);\n"
        "    }\n"
        "    free_scratch(&scratch);\n"
        "}\n"
        "void APIENTRY_GL4ES fpe_glDrawElementsInstanced("
    )
    new_inst_arr_call = (
        "        /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n"
        "        omwperf_live.real_draw_calls++;\n"
        "        omwperf_live.instance_iterations++;\n"
        "        gles_glDrawArrays(mode, first, count);\n"
        "    }\n"
        "    free_scratch(&scratch);\n"
        "}\n"
        "void APIENTRY_GL4ES fpe_glDrawElementsInstanced("
    )
    text = replace_once(text, old_inst_arr_call, new_inst_arr_call,
                        "fpe.c / DrawArraysInstanced per-iteration counter")

    # Anchor: the gles_glDrawElements call at the bottom of the per-instance loop
    # in fpe_glDrawElementsInstanced.
    old_inst_el_call = (
        "        gles_glDrawElements(mode, count, type, inds);\n"
        "    }\n"
        "    if(use_vbo)\n"
        "        wantBufferIndex(0);\n"
        "    free_scratch(&scratch);\n"
        "}"
    )
    new_inst_el_call = (
        "        /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n"
        "        omwperf_live.real_draw_calls++;\n"
        "        omwperf_live.instance_iterations++;\n"
        "        gles_glDrawElements(mode, count, type, inds);\n"
        "    }\n"
        "    if(use_vbo)\n"
        "        wantBufferIndex(0);\n"
        "    free_scratch(&scratch);\n"
        "}"
    )
    text = replace_once(text, old_inst_el_call, new_inst_el_call,
                        "fpe.c / DrawElementsInstanced per-iteration counter")

    write(fpe_c, text)
    print("Patched src/gl/fpe.c with per-instance iteration counters.")
else:
    print("src/gl/fpe.c perf counters already applied.")


# ---------------------------------------------------------------------------
# 5. Patch program.c — glUseProgram switch counter
# ---------------------------------------------------------------------------

text = read(program_c)

if MARKER not in text:
    text = replace_once(
        text,
        '#include "program.h"\n',
        '#include "program.h"\n'
        '/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n'
        '#include "omwperf.h"\n',
        "program.c / omwperf include",
    )

    # Anchor: the body of gl4es_glUseProgram where the program id actually changes.
    # We count every call where the new program id differs from the current one.
    old_useprogram = (
        "    noerrorShim();\n"
        "    DBG(printf(\"program id=%d\\n\", glprogram->id);)\n"
        "    glstate->glsl->program=glprogram->id;\n"
        "    glstate->glsl->glprogram=glprogram;\n"
        "}"
    )
    new_useprogram = (
        "    noerrorShim();\n"
        "    DBG(printf(\"program id=%d\\n\", glprogram->id);)\n"
        "    /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n"
        "    if (glstate->glsl->program != glprogram->id)\n"
        "        omwperf_live.program_switches++;\n"
        "    glstate->glsl->program=glprogram->id;\n"
        "    glstate->glsl->glprogram=glprogram;\n"
        "}"
    )
    text = replace_once(text, old_useprogram, new_useprogram,
                        "program.c / UseProgram switch counter")

    write(program_c, text)
    print("Patched src/gl/program.c with program-switch counter.")
else:
    print("src/gl/program.c program-switch counter already applied.")


# ---------------------------------------------------------------------------
# 6. Wire omwperf.c into Android.mk
# ---------------------------------------------------------------------------

text = read(android_mk)

if MARKER not in text:
    old_mk_tail = (
        "\tsrc/glx/hardext.c \\\n"
        "\tsrc/glx/glx.c \\\n"
        "\tsrc/glx/lookup.c \\\n"
        "\tsrc/glx/gbm.c \\\n"
        "\tsrc/glx/streaming.c \\\n"
    )
    new_mk_tail = (
        "\tsrc/glx/hardext.c \\\n"
        "\tsrc/glx/glx.c \\\n"
        "\tsrc/glx/lookup.c \\\n"
        "\tsrc/glx/gbm.c \\\n"
        "\tsrc/glx/streaming.c \\\n"
        "\tsrc/gl/omwperf.c \\\n"
        "# OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS\n"
    )
    text = replace_once(text, old_mk_tail, new_mk_tail, "Android.mk / add omwperf.c")
    write(android_mk, text)
    print("Patched Android.mk to compile omwperf.c.")
else:
    print("Android.mk already includes omwperf.c.")


# ---------------------------------------------------------------------------
# 7. Verify
# ---------------------------------------------------------------------------

for path, label in [
    (drawing_c, "drawing.c"),
    (fpe_c, "fpe.c"),
    (program_c, "program.c"),
    (android_mk, "Android.mk"),
    (omwperf_h, "omwperf.h"),
    (omwperf_c, "omwperf.c"),
]:
    content = read(path)
    if MARKER not in content:
        raise SystemExit(f"Verification failed: marker missing from {label}")

# Check functional anchors
drawing_text = read(drawing_c)
if "omwperf_live.real_draw_calls++" not in drawing_text:
    raise SystemExit("Verification failed: drawing.c missing real_draw_calls increment")
if "omwperf_live.instanced_arrays_calls++" not in drawing_text:
    raise SystemExit("Verification failed: drawing.c missing instanced_arrays_calls increment")

fpe_text = read(fpe_c)
if fpe_text.count("omwperf_live.instance_iterations++") != 2:
    raise SystemExit("Verification failed: fpe.c should have 2 instance_iterations increments")

program_text = read(program_c)
if "omwperf_live.program_switches++" not in program_text:
    raise SystemExit("Verification failed: program.c missing program_switches increment")

print()
print("GL4ES Phase 1 perf counters: PASS")
print("  drawing.c : real_draw_calls, vertices_submitted,")
print("              instanced_arrays_calls, instanced_elements_calls")
print("  fpe.c     : instance_iterations (x2, one per instanced function)")
print("  program.c : program_switches")
print("  omwperf.h / omwperf.c : counter struct + omwperf_snapshot()")
print("              + omwperf_log_gl_env_once() for GL_EXTENSIONS logcat dump")
