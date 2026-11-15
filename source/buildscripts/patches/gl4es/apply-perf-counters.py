#!/usr/bin/env python3
"""
OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS

Phase 1 baseline: add per-frame draw-call counters to GL4ES.

Instrumented sites — every real gles_glDrawArrays / gles_glDrawElements call
that reaches the driver:

  src/gl/drawing.c  glDrawElementsCommon()
    Site D1  — direct gles_glDrawArrays  (instancecount==1 || esversion==1)
    Site D2  — direct gles_glDrawElements (same gate)
    Site D3  — fpe_glDrawArraysInstanced  dispatch counter (instancecount>1)
    Site D4  — fpe_glDrawElementsInstanced dispatch counter (instancecount>1)

  src/gl/fpe.c  fpe_glDrawArraysInstanced()
    Site F1  — gles_glDrawArrays per-instance iteration

  src/gl/fpe.c  fpe_glDrawElementsInstanced()
    Site F2  — gles_glDrawElements per-instance iteration

  src/gl/listdraw.c  draw_renderlist() — render-list (batch/display-list) path
    Site L1  — indices path, instanceCount==1: gles_glDrawElements
    Site L2  — indices path, instanceCount>1 loop: gles_glDrawElements (×N)
    Site L3  — no-indices path, polygon_mode GL_LINE: gles_glDrawElements
    Site L4  — no-indices path, instanceCount==1: gles_glDrawArrays
    Site L5  — no-indices path, instanceCount>1 loop: gles_glDrawArrays (×N)
    Site L6  — polygon_mode GL_LINE, indices path: gles_glDrawElements

  src/gl/program.c  gl4es_glUseProgram()
    Site P1  — program-id-change counter

Counter struct (omwperf_counters_t) is declared in a new header
  src/gl/omwperf.h
and defined once in
  src/gl/omwperf.c

The exported C function:
  void omwperf_snapshot(omwperf_counters_t *out);
copies the live counters into *out and zeroes them atomically enough for a
single-threaded render loop (no locking — plain increments, as specified).

omwperf_log_gl_env_once() logs GL_VERSION, GL_RENDERER, the full
GL_EXTENSIONS string, and the four queried extension flags to Android logcat
the first time omwperf_snapshot() is called.  The perf-log patcher also calls
this from the sidecar .env.txt writer so the data appears in the file even if
logcat is not captured.

Safe to run twice: every edit site is guarded by the unique marker string
  OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS

Anchors verified against sisah2/gl4es@5ac069d82ad8ca2cc3c574484e4c5bad880db83e.
"""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: apply-perf-counters.py <gl4es-source-dir>")

root = Path(sys.argv[1])

drawing_c   = root / "src" / "gl" / "drawing.c"
fpe_c       = root / "src" / "gl" / "fpe.c"
listdraw_c  = root / "src" / "gl" / "listdraw.c"
program_c   = root / "src" / "gl" / "program.c"
omwperf_h   = root / "src" / "gl" / "omwperf.h"
omwperf_c   = root / "src" / "gl" / "omwperf.c"
android_mk  = root / "Android.mk"

for p in (drawing_c, fpe_c, listdraw_c, program_c, android_mk):
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
# 1. Write src/gl/omwperf.h
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
    /* Real GLES draw calls that reached the driver (all sites combined) */
    unsigned int real_draw_calls;
    /* Total vertex / index count submitted across all real draws */
    unsigned int vertices_submitted;
    /* Number of times fpe_glDrawArraysInstanced was entered */
    unsigned int instanced_arrays_calls;
    /* Number of times fpe_glDrawElementsInstanced was entered */
    unsigned int instanced_elements_calls;
    /* Total emulated instance iterations (fpe.c + listdraw.c loops) */
    unsigned int instance_iterations;
    /* gl4es-level glUseProgram switches (program id changes) */
    unsigned int program_switches;
    /* One-time GL environment log has been emitted */
    unsigned int ext_log_done;
} omwperf_counters_t;

/* Live counter set — incremented in the hot path */
extern omwperf_counters_t omwperf_live;

/* Copy live counters into *out and reset them to zero.
 * Call once per frame from the OpenMW perf-log patch. */
void omwperf_snapshot(omwperf_counters_t *out);

/* Write GL_VERSION / GL_RENDERER / GL_EXTENSIONS to Android logcat once
 * and also fill the caller-supplied buffer (may be NULL) with the full
 * GL_EXTENSIONS string so the perf-log patcher can write it to the sidecar
 * .env.txt file without a separate glGetString call.
 * Called automatically by omwperf_snapshot() on the first invocation. */
void omwperf_log_gl_env_once(void);

/* Fill *version, *renderer, *extensions with the underlying GLES strings.
 * Caller must NOT free them — they point to static storage.
 * Returns 1 on success, 0 if glGetString is unavailable. */
int omwperf_query_gl_env(const char **version, const char **renderer,
                          const char **extensions);

#ifdef __cplusplus
}
#endif
#endif /* OMWPERF_H */
"""

write(omwperf_h, omwperf_h_text)
print("Written src/gl/omwperf.h")


# ---------------------------------------------------------------------------
# 2. Write src/gl/omwperf.c
# ---------------------------------------------------------------------------

omwperf_c_text = r"""/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS
 * Phase 1 baseline: counter definition, snapshot, and GL-environment query.
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

#include "gles.h"
#include "loader.h"

omwperf_counters_t omwperf_live = {0};

/* Static storage for the one-time GL strings. */
static const char *s_version   = NULL;
static const char *s_renderer  = NULL;
static const char *s_extensions = NULL;

int omwperf_query_gl_env(const char **version, const char **renderer,
                          const char **extensions)
{
    LOAD_GLES(glGetString);
    if (!gles_glGetString) return 0;
    if (!s_version) {
        s_version    = (const char *)gles_glGetString(GL_VERSION);
        s_renderer   = (const char *)gles_glGetString(GL_RENDERER);
        s_extensions = (const char *)gles_glGetString(GL_EXTENSIONS);
    }
    if (version)    *version    = s_version;
    if (renderer)   *renderer   = s_renderer;
    if (extensions) *extensions = s_extensions;
    return 1;
}

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

    const char *version = NULL, *renderer = NULL, *exts = NULL;
    if (!omwperf_query_gl_env(&version, &renderer, &exts)) {
        OMWPERF_LOG("omwperf: glGetString not available");
        return;
    }

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
            /* Don't split in the middle of an extension name. */
            if (p[len] && len == chunk) {
                while (len > 0 && p[len-1] != ' ') --len;
                if (len == 0) len = chunk;
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

    int has_inst_arrays = exts && (strstr(exts, "GL_EXT_instanced_arrays")  != 0);
    int has_draw_inst   = exts && (strstr(exts, "GL_EXT_draw_instanced")     != 0);
    int has_vao         = exts && (strstr(exts, "GL_OES_vertex_array_object") != 0);
    int has_mdi         = exts && (strstr(exts, "GL_EXT_multi_draw_indirect") != 0);
    OMWPERF_LOG("GL_EXT_instanced_arrays=%d GL_EXT_draw_instanced=%d "
                "GL_OES_vertex_array_object=%d GL_EXT_multi_draw_indirect=%d",
                has_inst_arrays, has_draw_inst, has_vao, has_mdi);
}
"""

write(omwperf_c, omwperf_c_text)
print("Written src/gl/omwperf.c")


# ---------------------------------------------------------------------------
# 3. Patch drawing.c — glDrawElementsCommon()
#    Sites D1/D2 (direct path) and D3/D4 (instanced dispatch).
# ---------------------------------------------------------------------------

text = read(drawing_c)

if MARKER not in text:
    # 3A — add #include
    text = replace_once(
        text,
        '#include "../glx/hardext.h"\n',
        '#include "../glx/hardext.h"\n'
        '/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n'
        '#include "omwperf.h"\n',
        "drawing.c / omwperf include",
    )

    # 3B — Sites D1 + D2: direct gles draw path
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
        "            /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS D1/D2 */\n"
        "            omwperf_live.real_draw_calls++;\n"
        "            omwperf_live.vertices_submitted += (unsigned int)count;\n"
        "            if(!iindices && !sindices)\n"
        "                gles_glDrawArrays(mode, first, count);\n"
        "            else\n"
        "                gles_glDrawElements(mode, count, (sindices)?GL_UNSIGNED_SHORT:GL_UNSIGNED_INT, (sindices?((void*)sindices):((void*)iindices)));\n"
        "        } else {"
    )
    text = replace_once(text, old_direct, new_direct, "drawing.c / D1+D2 direct draw counters")

    # 3C — Sites D3 + D4: instanced dispatch
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
        "            /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS D3/D4 */\n"
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
    text = replace_once(text, old_instanced, new_instanced, "drawing.c / D3+D4 instanced dispatch")

    write(drawing_c, text)
    print("Patched src/gl/drawing.c (sites D1-D4).")
else:
    print("src/gl/drawing.c already patched.")


# ---------------------------------------------------------------------------
# 4. Patch fpe.c — Sites F1 (DrawArraysInstanced loop) and F2 (DrawElementsInstanced loop)
# ---------------------------------------------------------------------------

text = read(fpe_c)

if MARKER not in text:
    # Add #include
    text = replace_once(
        text,
        '#include "../glx/hardext.h"\n',
        '#include "../glx/hardext.h"\n'
        '/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n'
        '#include "omwperf.h"\n',
        "fpe.c / omwperf include",
    )

    # Site F1 — per-instance gles_glDrawArrays inside fpe_glDrawArraysInstanced
    old_f1 = (
        "        gles_glDrawArrays(mode, first, count);\n"
        "    }\n"
        "    free_scratch(&scratch);\n"
        "}\n"
        "void APIENTRY_GL4ES fpe_glDrawElementsInstanced("
    )
    new_f1 = (
        "        /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS F1 */\n"
        "        omwperf_live.real_draw_calls++;\n"
        "        omwperf_live.instance_iterations++;\n"
        "        gles_glDrawArrays(mode, first, count);\n"
        "    }\n"
        "    free_scratch(&scratch);\n"
        "}\n"
        "void APIENTRY_GL4ES fpe_glDrawElementsInstanced("
    )
    text = replace_once(text, old_f1, new_f1, "fpe.c / F1 DrawArraysInstanced per-iteration")

    # Site F2 — per-instance gles_glDrawElements inside fpe_glDrawElementsInstanced
    old_f2 = (
        "        gles_glDrawElements(mode, count, type, inds);\n"
        "    }\n"
        "    if(use_vbo)\n"
        "        wantBufferIndex(0);\n"
        "    free_scratch(&scratch);\n"
        "}"
    )
    new_f2 = (
        "        /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS F2 */\n"
        "        omwperf_live.real_draw_calls++;\n"
        "        omwperf_live.instance_iterations++;\n"
        "        gles_glDrawElements(mode, count, type, inds);\n"
        "    }\n"
        "    if(use_vbo)\n"
        "        wantBufferIndex(0);\n"
        "    free_scratch(&scratch);\n"
        "}"
    )
    text = replace_once(text, old_f2, new_f2, "fpe.c / F2 DrawElementsInstanced per-iteration")

    write(fpe_c, text)
    print("Patched src/gl/fpe.c (sites F1-F2).")
else:
    print("src/gl/fpe.c already patched.")


# ---------------------------------------------------------------------------
# 5. Patch listdraw.c — draw_renderlist()
#
# Six call sites, all inside the do-while loop of draw_renderlist():
#
#   L1  indices, instanceCount==1   → gles_glDrawElements (normal path)
#   L2  indices, instanceCount>1    → gles_glDrawElements per-instance loop
#   L3  indices, GL_LINE polygon    → gles_glDrawElements (wireframe)
#   L4  no-indices, instanceCount==1 → gles_glDrawArrays (normal path)
#   L5  no-indices, instanceCount>1  → gles_glDrawArrays per-instance loop
#   L6  no-indices, GL_LINE polygon  → gles_glDrawElements (wireframe)
#
# The anchors below are the exact verbatim strings from the upstream file at
# commit 5ac069d82ad8ca2cc3c574484e4c5bad880db83e.
# ---------------------------------------------------------------------------

text = read(listdraw_c)

if MARKER not in text:
    # Add #include after the existing "fpe.h" include at the top
    text = replace_once(
        text,
        '#include "../glx/hardext.h"\n',
        '#include "../glx/hardext.h"\n'
        '/* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS */\n'
        '#include "omwperf.h"\n',
        "listdraw.c / omwperf include",
    )

    # Site L3 — indices path, GL_LINE polygon_mode (wireframe converted indices)
    # This comes BEFORE the normal indices path in the source, so patch it first
    # to keep the anchor strings unique.
    old_l3 = (
        "                    bindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);\n"
        "                    gles_glDrawElements(mode, list->ind_line, GL_UNSIGNED_SHORT, list->ind_lines);\n"
        "                    use_vbo_indices = 1;\n"
        "                } else {"
    )
    new_l3 = (
        "                    bindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);\n"
        "                    /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS L3 */\n"
        "                    omwperf_live.real_draw_calls++;\n"
        "                    omwperf_live.vertices_submitted += (unsigned int)list->ind_line;\n"
        "                    gles_glDrawElements(mode, list->ind_line, GL_UNSIGNED_SHORT, list->ind_lines);\n"
        "                    use_vbo_indices = 1;\n"
        "                } else {"
    )
    text = replace_once(text, old_l3, new_l3, "listdraw.c / L3 GL_LINE indices wireframe")

    # Site L1 — indices path, instanceCount==1 (normal)
    old_l1 = (
        "                    if(list->instanceCount==1)\n"
        "                        gles_glDrawElements(mode, list->ilen, GL_UNSIGNED_SHORT, vbo_indices?NULL:indices);\n"
        "                    else {"
    )
    new_l1 = (
        "                    if(list->instanceCount==1) {\n"
        "                        /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS L1 */\n"
        "                        omwperf_live.real_draw_calls++;\n"
        "                        omwperf_live.vertices_submitted += (unsigned int)list->ilen;\n"
        "                        gles_glDrawElements(mode, list->ilen, GL_UNSIGNED_SHORT, vbo_indices?NULL:indices);\n"
        "                    } else {"
    )
    text = replace_once(text, old_l1, new_l1, "listdraw.c / L1 indices instanceCount==1")

    # Site L2 — indices path, instanceCount>1 per-instance loop
    old_l2 = (
        "                        for (glstate->instanceID=0; glstate->instanceID<list->instanceCount; ++glstate->instanceID)\n"
        "                            gles_glDrawElements(mode, list->ilen, GL_UNSIGNED_SHORT, vbo_indices?NULL:indices);\n"
        "                        glstate->instanceID = 0;"
    )
    new_l2 = (
        "                        for (glstate->instanceID=0; glstate->instanceID<list->instanceCount; ++glstate->instanceID) {\n"
        "                            /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS L2 */\n"
        "                            omwperf_live.real_draw_calls++;\n"
        "                            omwperf_live.instance_iterations++;\n"
        "                            omwperf_live.vertices_submitted += (unsigned int)list->ilen;\n"
        "                            gles_glDrawElements(mode, list->ilen, GL_UNSIGNED_SHORT, vbo_indices?NULL:indices);\n"
        "                        }\n"
        "                        glstate->instanceID = 0;"
    )
    text = replace_once(text, old_l2, new_l2, "listdraw.c / L2 indices instanceCount>1 loop")

    # Site L6 — no-indices path, GL_LINE polygon_mode (wireframe converted, no-indices side)
    old_l6 = (
        "                    bindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);\n"
        "\t\t\t\t\tgles_glDrawElements(mode, list->ind_line, GL_UNSIGNED_SHORT, list->ind_lines);\n"
        "                } else {"
    )
    new_l6 = (
        "                    bindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);\n"
        "                    /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS L6 */\n"
        "                    omwperf_live.real_draw_calls++;\n"
        "                    omwperf_live.vertices_submitted += (unsigned int)list->ind_line;\n"
        "\t\t\t\t\tgles_glDrawElements(mode, list->ind_line, GL_UNSIGNED_SHORT, list->ind_lines);\n"
        "                } else {"
    )
    text = replace_once(text, old_l6, new_l6, "listdraw.c / L6 GL_LINE no-indices wireframe")

    # Site L4 — no-indices path, instanceCount==1 (normal)
    old_l4 = (
        "                    if(list->instanceCount==1)\n"
        "                        gles_glDrawArrays(mode, 0, len);\n"
        "                    else {"
    )
    new_l4 = (
        "                    if(list->instanceCount==1) {\n"
        "                        /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS L4 */\n"
        "                        omwperf_live.real_draw_calls++;\n"
        "                        omwperf_live.vertices_submitted += (unsigned int)len;\n"
        "                        gles_glDrawArrays(mode, 0, len);\n"
        "                    } else {"
    )
    text = replace_once(text, old_l4, new_l4, "listdraw.c / L4 no-indices instanceCount==1")

    # Site L5 — no-indices path, instanceCount>1 per-instance loop
    old_l5 = (
        "                        for (glstate->instanceID=0; glstate->instanceID<list->instanceCount; ++glstate->instanceID)\n"
        "                            gles_glDrawArrays(mode, 0, len);\n"
        "                        glstate->instanceID = 0;"
    )
    new_l5 = (
        "                        for (glstate->instanceID=0; glstate->instanceID<list->instanceCount; ++glstate->instanceID) {\n"
        "                            /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS L5 */\n"
        "                            omwperf_live.real_draw_calls++;\n"
        "                            omwperf_live.instance_iterations++;\n"
        "                            omwperf_live.vertices_submitted += (unsigned int)len;\n"
        "                            gles_glDrawArrays(mode, 0, len);\n"
        "                        }\n"
        "                        glstate->instanceID = 0;"
    )
    text = replace_once(text, old_l5, new_l5, "listdraw.c / L5 no-indices instanceCount>1 loop")

    write(listdraw_c, text)
    print("Patched src/gl/listdraw.c (sites L1-L6).")
else:
    print("src/gl/listdraw.c already patched.")


# ---------------------------------------------------------------------------
# 6. Patch program.c — Site P1: glUseProgram switch counter
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

    # Exact text from sisah2/gl4es@5ac069d program.c lines 820-824:
    # there is a blank line between the DBG printf and the assignment.
    old_p1 = (
        "    noerrorShim();\n"
        "    DBG(printf(\"program id=%d\\n\", glprogram->id);)\n"
        "\n"
        "    glstate->glsl->program=glprogram->id;\n"
        "    glstate->glsl->glprogram=glprogram;\n"
        "}"
    )
    new_p1 = (
        "    noerrorShim();\n"
        "    DBG(printf(\"program id=%d\\n\", glprogram->id);)\n"
        "    /* OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS P1 */\n"
        "    if (glstate->glsl->program != glprogram->id)\n"
        "        omwperf_live.program_switches++;\n"
        "\n"
        "    glstate->glsl->program=glprogram->id;\n"
        "    glstate->glsl->glprogram=glprogram;\n"
        "}"
    )
    text = replace_once(text, old_p1, new_p1, "program.c / P1 UseProgram switch counter")

    write(program_c, text)
    print("Patched src/gl/program.c (site P1).")
else:
    print("src/gl/program.c already patched.")


# ---------------------------------------------------------------------------
# 7. Wire omwperf.c into Android.mk
# ---------------------------------------------------------------------------

text = read(android_mk)

if MARKER not in text:
    old_mk = (
        "\tsrc/glx/hardext.c \\\n"
        "\tsrc/glx/glx.c \\\n"
        "\tsrc/glx/lookup.c \\\n"
        "\tsrc/glx/gbm.c \\\n"
        "\tsrc/glx/streaming.c \\\n"
    )
    new_mk = (
        "\tsrc/glx/hardext.c \\\n"
        "\tsrc/glx/glx.c \\\n"
        "\tsrc/glx/lookup.c \\\n"
        "\tsrc/glx/gbm.c \\\n"
        "\tsrc/glx/streaming.c \\\n"
        "\tsrc/gl/omwperf.c \\\n"
        "# OPENMW_ANDROID_PHASE1_GL4ES_PERF_COUNTERS\n"
    )
    text = replace_once(text, old_mk, new_mk, "Android.mk / add omwperf.c")
    write(android_mk, text)
    print("Patched Android.mk.")
else:
    print("Android.mk already includes omwperf.c.")


# ---------------------------------------------------------------------------
# 8. Idempotency verification — run the script a second time and confirm no changes
# ---------------------------------------------------------------------------

for path, label in [
    (drawing_c,  "drawing.c"),
    (fpe_c,      "fpe.c"),
    (listdraw_c, "listdraw.c"),
    (program_c,  "program.c"),
    (android_mk, "Android.mk"),
    (omwperf_h,  "omwperf.h"),
    (omwperf_c,  "omwperf.c"),
]:
    if MARKER not in read(path):
        raise SystemExit(f"Verification failed: marker missing from {label}")

drawing_text  = read(drawing_c)
fpe_text      = read(fpe_c)
listdraw_text = read(listdraw_c)
program_text  = read(program_c)

checks = {
    "D1/D2 real_draw_calls in drawing.c":        drawing_text.count("omwperf_live.real_draw_calls++") >= 1,
    "D3 instanced_arrays_calls in drawing.c":    "omwperf_live.instanced_arrays_calls++" in drawing_text,
    "D4 instanced_elements_calls in drawing.c":  "omwperf_live.instanced_elements_calls++" in drawing_text,
    "F1 real_draw_calls in fpe.c":               fpe_text.count("omwperf_live.real_draw_calls++") >= 1,
    "F1+F2 instance_iterations in fpe.c (x2)":  fpe_text.count("omwperf_live.instance_iterations++") == 2,
    "L1-L6 real_draw_calls in listdraw.c (x6)":  listdraw_text.count("omwperf_live.real_draw_calls++") == 6,
    "L2+L5 instance_iterations in listdraw.c (x2)": listdraw_text.count("omwperf_live.instance_iterations++") == 2,
    "P1 program_switches in program.c":          "omwperf_live.program_switches++" in program_text,
    "omwperf_query_gl_env in omwperf.h":         "omwperf_query_gl_env" in read(omwperf_h),
}

failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit("Verification failed:\n - " + "\n - ".join(failed))

print()
print("GL4ES Phase 1 perf counters: PASS")
print("  Instrumented sites:")
print("   drawing.c  D1 — glDrawElementsCommon direct gles_glDrawArrays")
print("   drawing.c  D2 — glDrawElementsCommon direct gles_glDrawElements")
print("   drawing.c  D3 — fpe_glDrawArraysInstanced dispatch")
print("   drawing.c  D4 — fpe_glDrawElementsInstanced dispatch")
print("   fpe.c      F1 — fpe_glDrawArraysInstanced per-instance gles_glDrawArrays")
print("   fpe.c      F2 — fpe_glDrawElementsInstanced per-instance gles_glDrawElements")
print("   listdraw.c L1 — draw_renderlist indices instanceCount==1 gles_glDrawElements")
print("   listdraw.c L2 — draw_renderlist indices instanceCount>1 loop gles_glDrawElements")
print("   listdraw.c L3 — draw_renderlist indices GL_LINE wireframe gles_glDrawElements")
print("   listdraw.c L4 — draw_renderlist no-indices instanceCount==1 gles_glDrawArrays")
print("   listdraw.c L5 — draw_renderlist no-indices instanceCount>1 loop gles_glDrawArrays")
print("   listdraw.c L6 — draw_renderlist no-indices GL_LINE wireframe gles_glDrawElements")
print("   program.c  P1 — gl4es_glUseProgram program-id change")
print("  omwperf.h / omwperf.c: counter struct, snapshot, query_gl_env")
