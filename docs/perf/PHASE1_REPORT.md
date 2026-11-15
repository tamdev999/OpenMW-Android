# OpenMW Android — Phase 1 Baseline Report

**Branch:** `perf/phase1-baseline`  
**Date:** 2026-11-15  
**Target device:** AYN Thor, Snapdragon 8 Gen 2, Adreno 740  
**Engine commit (OpenMW):** `f4bec41444214a7903bebd178389ca22ca13f646` (OpenMW 0.51.0 Final)  
**GL4ES commit (sisah2/gl4es):** `5ac069d82ad8ca2cc3c574484e4c5bad880db83e`  
**OSG commit (openmw/osg):** `69cfecebfb6dc703b42e8de39eed750a84a87489`

---

## Table of Contents
1. [Build Record](#1-build-record)
2. [Deliverables](#2-deliverables)
3. [Step 2 — GL4ES Draw Counters: Instrumented Sites](#3-step-2--gl4es-draw-counters-instrumented-sites)
4. [Step 3 — OpenMW Frame Log](#4-step-3--openmw-frame-log)
5. [Step 4 — GL Environment Capture](#5-step-4--gl-environment-capture)
6. [Step 5 — Assumption Verification (A–G)](#6-step-5--assumption-verification-ag)
7. [Step 6 — Baseline Tables](#7-step-6--baseline-tables)
8. [Discrepancies and Blockers](#8-discrepancies-and-blockers)

---

## 1. Build Record

### 1.1 Pinned source commits

| Dependency | Commit / version | Source |
|---|---|---|
| OpenMW | `f4bec41444214a7903bebd178389ca22ca13f646` | `CMakeLists.txt:OPENMW_VERSION` |
| OSG (openmw/osg) | `69cfecebfb6dc703b42e8de39eed750a84a87489` | `CMakeLists.txt:OSG_VERSION` |
| GL4ES (sisah2/gl4es) | `5ac069d82ad8ca2cc3c574484e4c5bad880db83e` | `CMakeLists.txt:GL4ES_VERSION` |
| NDK | r26b, SHA-256 `ad73c0370f0b0a87d1671ed2fd5a9ac9acfd1eb5c43a7fbfbd330f85d19dd632` | `include/version.sh:1–2` |
| Android API | 21 | `include/version.sh:3` |
| ABI | arm64-v8a | `include/version.sh:9–10` |
| NDK triplet | aarch64-linux-android | `include/version.sh:11` |

### 1.2 Compiler flags (`source/buildscripts/build.sh:66–90`)

```
CFLAGS   = -fPIC -O3 -flto
CXXFLAGS = -fPIC -frtti -fexceptions -O3 -flto
LDFLAGS  = -fPIC -Wl,--undefined-version -flto -Wl,-plugin-opt=-emulated-tls -fuse-ld=lld
```

Release mode adds `-O3` (`build.sh:68–69`). `--lto` adds `-flto` and forces `lld`
(`build.sh:72–76`).

### 1.3 Build commands (run on a Linux / WSL machine)

**Perf-instrumented build:**
```bash
cd source/buildscripts
./build.sh --arch arm64 --lto --ccache
# After native build completes:
cd ..
./gradlew assembleDebug
# Output: source/app/build/outputs/apk/debug/app-debug.apk
# Rename to: openmw-perf.apk
```

**Stock build (perf patchers removed):**
```bash
cd source/buildscripts
# Temporarily remove the two perf patchers from CMakeLists.txt:
#   gl4es PATCH_COMMAND: remove the apply-perf-counters.py line
#   OPENMW_PATCH: remove the apply-android-perf-log.py line
./clean.sh --build
./build.sh --arch arm64 --lto --ccache
cd ..
./gradlew assembleDebug
# Output: source/app/build/outputs/apk/debug/app-debug.apk
# Rename to: openmw-stock.apk
# Restore CMakeLists.txt (git checkout source/buildscripts/CMakeLists.txt)
```

**Record after each build:**
```bash
sha256sum source/app/src/main/jniLibs/arm64-v8a/libopenmw.so
ls -lh source/app/build/outputs/apk/debug/app-debug.apk
source/buildscripts/toolchain/arm64/bin/aarch64-linux-android-clang --version
```

### 1.4 Build status

**BLOCKED — WSL has no installed distributions on the build machine.**

`wsl --status` returned: *"Windows Subsystem for Linux has no installed distributions."*

The build requires Linux or WSL. No APK sha256 or file size can be reported
until the user runs the commands in §1.3 on a Linux machine.

### 1.5 APK installation safety note

Installing a differently-signed APK requires uninstalling the existing one.
**Back up saves and config before uninstalling:**
```bash
adb shell "run-as org.libsdl.app tar -czf /sdcard/openmw_backup.tar.gz \
    /data/data/org.libsdl.app/files/"
adb pull /sdcard/openmw_backup.tar.gz .
```
Then:
```bash
adb uninstall org.libsdl.app
adb install openmw-perf.apk
```

---

## 2. Deliverables

| File | Purpose |
|---|---|
| `source/buildscripts/patches/gl4es/apply-perf-counters.py` | GL4ES draw-call counter instrumentation (13 sites) |
| `source/buildscripts/patches/openmw051-final/apply-android-perf-log.py` | OpenMW per-frame CSV log + sidecar .env.txt |
| `source/buildscripts/CMakeLists.txt` | Both patchers wired into build |
| `docs/perf/PHASE1_REPORT.md` | This report |
| `docs/perf/DEVICE_RUNBOOK.md` | Step-by-step device instructions |
| `docs/perf/*.csv` | Raw baseline CSVs (populated by user after device runs) |

**CMakeLists.txt wiring:**
```cmake
# gl4es PATCH_COMMAND (source/buildscripts/CMakeLists.txt):
python3 .../patches/gl4es/apply-powervr-packed-depth-stencil.py <SOURCE_DIR> &&
python3 .../patches/gl4es/apply-perf-counters.py <SOURCE_DIR>

# OPENMW_PATCH (appended last):
... &&
python3 .../patches/openmw051-final/apply-android-perf-log.py <SOURCE_DIR>
```

---

## 3. Step 2 — GL4ES Draw Counters: Instrumented Sites

All sites verified against **sisah2/gl4es@5ac069d** by reading the raw GitHub
content. The patcher is idempotent: re-running it on an already-patched tree
prints "already patched" and exits cleanly.

### 3.1 Site table

| ID | File | Function | What is counted | gles call |
|---|---|---|---|---|
| **D1** | `src/gl/drawing.c` | `glDrawElementsCommon()` | Direct draw: `instancecount==1 \|\| esversion==1`, array path | `gles_glDrawArrays` |
| **D2** | `src/gl/drawing.c` | `glDrawElementsCommon()` | Direct draw: same gate, indexed path | `gles_glDrawElements` |
| **D3** | `src/gl/drawing.c` | `glDrawElementsCommon()` | Instanced array dispatch (`instancecount>1 && esversion>1`) | calls `fpe_glDrawArraysInstanced` |
| **D4** | `src/gl/drawing.c` | `glDrawElementsCommon()` | Instanced indexed dispatch (same gate) | calls `fpe_glDrawElementsInstanced` |
| **F1** | `src/gl/fpe.c` | `fpe_glDrawArraysInstanced()` | Each iteration of the per-instance emulation loop | `gles_glDrawArrays` |
| **F2** | `src/gl/fpe.c` | `fpe_glDrawElementsInstanced()` | Each iteration of the per-instance emulation loop | `gles_glDrawElements` |
| **L1** | `src/gl/listdraw.c` | `draw_renderlist()` | Render-list: indexed path, `instanceCount==1` (normal) | `gles_glDrawElements` |
| **L2** | `src/gl/listdraw.c` | `draw_renderlist()` | Render-list: indexed path, `instanceCount>1` per-instance loop | `gles_glDrawElements` ×N |
| **L3** | `src/gl/listdraw.c` | `draw_renderlist()` | Render-list: indexed path, `polygon_mode GL_LINE` wireframe | `gles_glDrawElements` |
| **L4** | `src/gl/listdraw.c` | `draw_renderlist()` | Render-list: no-indices path, `instanceCount==1` (normal) | `gles_glDrawArrays` |
| **L5** | `src/gl/listdraw.c` | `draw_renderlist()` | Render-list: no-indices path, `instanceCount>1` per-instance loop | `gles_glDrawArrays` ×N |
| **L6** | `src/gl/listdraw.c` | `draw_renderlist()` | Render-list: no-indices path, `polygon_mode GL_LINE` wireframe | `gles_glDrawElements` |
| **P1** | `src/gl/program.c` | `gl4es_glUseProgram()` | Program-id change (switch counter) | — |

Sites D1 and D2 share one counter increment because `glDrawElementsCommon` issues
exactly one real draw call per entry when `instancecount==1`.

Sites D3 and D4 count the *dispatch* to `fpe_glDrawArraysInstanced`/`fpe_glDrawElementsInstanced`
rather than individual draws; the per-draw increments come from F1/F2.

Sites L2 and L5 also increment `instance_iterations` once per loop iteration.

### 3.2 Counter struct (`src/gl/omwperf.h`)

```c
typedef struct {
    unsigned int real_draw_calls;        // all sites combined
    unsigned int vertices_submitted;     // count or index-count per draw
    unsigned int instanced_arrays_calls; // D3: fpe_glDrawArraysInstanced entries
    unsigned int instanced_elements_calls; // D4: fpe_glDrawElementsInstanced entries
    unsigned int instance_iterations;    // F1+F2+L2+L5: per-instance loop ticks
    unsigned int program_switches;       // P1: program-id changes
    unsigned int ext_log_done;           // internal one-shot flag
} omwperf_counters_t;
```

### 3.3 Exported API

```c
// Defined in src/gl/omwperf.c; linked into libGL.so via Android.mk.
void omwperf_snapshot(omwperf_counters_t *out); // copy + reset counters
int  omwperf_query_gl_env(const char **version,
                           const char **renderer,
                           const char **extensions); // returns 1 on success
```

`omwperf_snapshot()` also calls `omwperf_log_gl_env_once()` on its first
invocation, which writes GL_VERSION / GL_RENDERER / GL_EXTENSIONS (in 512-char
slices) to Android logcat with tag `OpenMW-GL4ES-Perf`.

---

## 4. Step 3 — OpenMW Frame Log

### 4.1 Activation

Set `OPENMW_PERF_LOG` via the launcher's **Custom Environment Flags** field
(key `envLine`, parsed in `GameActivity.loadLibraries()`):

```
OPENMW_PERF_LOG=/sdcard/Android/data/com.ast.openmw/files/OpenMW/perf/
```

If the value ends in `/` or is an existing directory, the engine writes
`perf-<unix-timestamp>.csv` inside it so each run gets its own file.
If the value is a file path, that file is used directly (overwritten).

### 4.2 CSV format

Header row:
```
frame_no,frame_ms,update_ms,cull_ms,draw_ms,real_draws,vertices,inst_arr,inst_el,inst_iters,prog_sw
```

One data row per completed frame. Example:
```
1024,8,3,2,1,312,48000,0,4,2048,7
```

Lines beginning with `#` are annotations:
```
# cell=Seyda Neen
```

Rows are buffered and **flushed to disk every 300 frames** and at engine exit.

### 4.3 Sidecar environment file

On the first frame the engine also writes `<csvpath>.env.txt` containing:

```
GL_VERSION=OpenGL ES 2.0 ...
GL_RENDERER=Adreno (TM) 740
GL_EXTENSIONS=GL_EXT_instanced_arrays GL_OES_...
GL_EXT_instanced_arrays=1
GL_EXT_draw_instanced=1
GL_OES_vertex_array_object=1
GL_EXT_multi_draw_indirect=0
lighting_method=shaders compatibility
threading_model=CullDrawThreadPerContext
object_paging=false
groundcover=false
```

This file is written by `PerfLog::writeEnv()` via `omwperf_query_gl_env()`
— it does **not** require logcat to be captured.

### 4.4 OSG timing stats

Update / cull / draw milliseconds come from `osg::Stats::getAttribute()` with
a 3-frame delay identical to the `OPENMW_OSG_STATS_FILE` path in `go()`.
Attribute names used (confirmed in `apps/openmw/engine.cpp`):

| Column | Primary stat name | Fallback |
|---|---|---|
| `update_ms` | `"Update"` (osgViewer) | `"world_time_taken"` (ScopedProfile) |
| `cull_ms` | `"Cull"` | — |
| `draw_ms` | `"Draw"` | — |

### 4.5 Startup metadata

Logged to the sidecar before the first CSV row:
- `lighting_method` — from `LightManager::getLightingMethodString()` via a
  `TRAVERSE_ALL_CHILDREN` NodeVisitor walk (`engine.cpp`, `go()`)
- `threading_model` — from `mViewer->getThreadingModel()`
- `object_paging` — from `Settings::terrain().mObjectPaging`
- `groundcover` — from `Settings::groundcover().mEnabled`

Cell changes are written as `# cell=<name>` annotations in the CSV via
`mPerfLogPendingCell` (set by the caller, consumed at frame bottom).

---

## 5. Step 4 — GL Environment Capture

### 5.1 Two capture paths

**Path A — sidecar file (preferred):**  
`<OPENMW_PERF_LOG path>.env.txt` written by `PerfLog::writeEnv()` on the
first frame. Contains GL_VERSION, GL_RENDERER, full GL_EXTENSIONS string,
and the four specific extension flags.

**Path B — logcat:**
```bash
adb logcat -s "OpenMW-GL4ES-Perf:I" "OpenMW-Native:I"
```
GL4ES logs `omwperf_log_gl_env_once()` output with tag `OpenMW-GL4ES-Perf` on
the first `omwperf_snapshot()` call.

### 5.2 Expected values on Adreno 740 ES2 context (not yet measured)

| Item | Expected | Measured |
|---|---|---|
| GL_RENDERER | Adreno (TM) 740 | **TODO** — read from .env.txt |
| GL_VERSION | OpenGL ES 2.0 Qualcomm... | **TODO** |
| GL_EXT_instanced_arrays | 1 | **TODO** |
| GL_EXT_draw_instanced | 1 | **TODO** |
| GL_OES_vertex_array_object | 1 | **TODO** |
| GL_EXT_multi_draw_indirect | 0 (ES2 context) | **TODO** |

---

## 6. Step 5 — Assumption Verification (A–G)

All citations are to the **pinned upstream sources** at their exact commits.

---

### A. fpe_glDrawElementsInstanced / fpe_glDrawArraysInstanced issue one GLES draw per instance; divisor attributes fed through glVertexAttrib4fv

**Confirmed.** `src/gl/fpe.c` — `fpe_glDrawArraysInstanced()`:

```c
// fpe.c (approx. line 50-110 at commit 5ac069d)
for (GLint id=0; id<primcount; ++id) {
    GoUniformiv(glprogram, glprogram->builtin_instanceID, 1, 1, &id);
    for(int i=0; i<hardext.maxvattrib; i++)
    if(glprogram->va_size[i]) {
        vertexattrib_t *w = &glstate->vao->vertexattrib[i];
        if(w->divisor && w->enabled) {
            char* current = (char*)((uintptr_t)w->pointer +
                                   ((w->buffer)?(uintptr_t)w->buffer->data:0));
            int stride = w->stride;
            if(!stride) stride = gl_sizeof(w->type)*w->size;
            current += (id/w->divisor) * stride;
            // ... convert to float ...
            gles_glVertexAttrib4fv(i, glstate->gleshard->vavalue[i]);
        }
    }
    gles_glDrawArrays(mode, first, count);  // one real draw per iteration
}
```

`fpe_glDrawElementsInstanced` is identical in structure, calling
`gles_glDrawElements` per iteration instead (`src/gl/fpe.c` ~line 112–180).

**vertexattrib_t** (`src/gl/buffers.h`):

| Field | Type | Purpose |
|---|---|---|
| `divisor` | `GLint` | 0 = per-vertex; >0 = advance every N instances |
| `buffer` | `glbuffer_t*` | reference VBO; if non-NULL, `pointer` is relative to `buffer->data` |
| `real_buffer` | `GLuint` | underlying GLES VBO name (after upload) |
| `real_pointer` | `const GLvoid*` | byte offset within `real_buffer` |

**Dispatch condition** (`src/gl/drawing.c` — `glDrawElementsCommon()`):

```c
if(instancecount==1 || hardext.esversion==1) {
    // Site D1/D2: direct gles draw
} else {
    // Sites D3/D4: dispatch to fpe instancing emulation
    fpe_glDrawArraysInstanced(...) or fpe_glDrawElementsInstanced(...)
}
```

Condition: `instancecount > 1 AND hardext.esversion > 1`. With `LIBGL_ES=2`,
`hardext.esversion==2`, so groundcover geometry takes the instanced path.

---

### B. LIBGL_ES accepted values; hardext.esversion > 2 gates; Android.mk build flags

**LIBGL_ES accepted values** (`src/gl/init.c`):

```c
globals4es.es = ReturnEnvVarInt("LIBGL_ES");
switch(globals4es.es) {
    case 1:
    case 2:   break;
    default:  globals4es.es = DEFAULT_ES;  break;
}
```

Values `1` and `2` are accepted. Value `3` or any other value falls to
`default:` and is replaced by `DEFAULT_ES`. On Android,
`Android.mk` defines `-DDEFAULT_ES=2`, so `LIBGL_ES=3` → ES2.

**hardext.esversion > 2 gates** (`src/glx/hardext.c`):

```c
if (hardext.esversion>2) {
    hardext.drawbuffers = 1;   // GL_EXT_draw_buffers treated as core
} else {
    S("GL_EXT_draw_buffers ", drawbuffers, 1);
}
// GLSL version probing (esversion > 1 gate):
if(hardext.esversion>1) {
    if(testGLSL("#version 120", 1))     hardext.glsl120 = 1;
    if(testGLSL("#version 300 es", 0))  hardext.glsl300es = 1;
    if(testGLSL("#version 310 es", 1))  hardext.glsl310es = 1;
}
```

With ES2 (`esversion==2`): `glsl300es` and `glsl310es` are probed but their
in-core behaviour is not assumed; `drawbuffers` falls through to the extension
string check.

**Android.mk flags** (confirmed from raw file at pinned commit):

```makefile
LOCAL_CFLAGS += -g -std=gnu99 -funwind-tables -O3 -fvisibility=hidden
LOCAL_CFLAGS += -DNOX11          # no X11 windowing
LOCAL_CFLAGS += -DNO_GBM         # no GBM/DRM
LOCAL_CFLAGS += -DUSE_ANDROID_LOG=1
LOCAL_CFLAGS += -DDEFAULT_ES=2   # ES2 default when LIBGL_ES unrecognised
```

No `NOEGL` flag: EGL is used (the `#ifdef NOEGL` branch in hardext.c is not
taken). No `STATICLIB`: shared library (`BUILD_SHARED_LIBRARY`).

---

### C. EGL context creation — OPENMW_GLES_VERSION mapping

File: `components/sdlutil/sdlgraphicswindow.cpp` (OpenMW commit f4bec41,
in the openmw/osg fork at OSG commit 69cfeceb):

```cpp
#if defined(ANDROID) || defined(OPENMW_GL4ES_MANUAL_INIT)
    int major = 1, minor = 1;
    char* ver = getenv("OPENMW_GLES_VERSION");
    if (ver && strcmp(ver, "2") == 0)      { major = 2; minor = 0; }
    else if (ver && strcmp(ver, "3") == 0) { major = 3; minor = 2; }
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_ES);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, major);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, minor);
#endif
    mContext = SDL_GL_CreateContext(mWindow);
```

Mapping:
- `OPENMW_GLES_VERSION=2` → `SDL_GL_CONTEXT_MAJOR_VERSION=2`, `MINOR=0` → ES 2.0 context
- `OPENMW_GLES_VERSION=3` → major=3, minor=2 → ES 3.2 (not used in this port)
- Unset → major=1, minor=1 → ES 1.1

Since `GameActivity.kt:loadLibraries()` unconditionally calls
`Os.setenv("OPENMW_GLES_VERSION", "2", true)`, the EGL context is always ES 2.0.
GL4ES then operates in ES2 mode (`LIBGL_ES=2`).

---

### D. OSG: vertex array objects, GL1 profile, GLES3 options

**GL1 profile / fixed function** — confirmed in
`source/buildscripts/CMakeLists.txt`:

```cmake
-DOPENGL_PROFILE="GL1"
-DOSG_GL1_AVAILABLE=ON
-DOSG_GL2_AVAILABLE=OFF
-DOSG_GL3_AVAILABLE=OFF
-DOSG_GLES1_AVAILABLE=OFF
-DOSG_GLES2_AVAILABLE=OFF
-DOSG_GL_DISPLAYLISTS_AVAILABLE=OFF
-DOSG_GL_VERTEX_FUNCS_AVAILABLE=ON
-DOSG_GL_VERTEX_ARRAY_FUNCS_AVAILABLE=ON
-DOSG_GL_FIXED_FUNCTION_AVAILABLE=ON
```

**Vertex array objects with GL1 profile:** With `OPENGL_PROFILE=GL1` and
`-DOSG_GLES2_AVAILABLE=OFF`, OSG's Geometry drawing uses the legacy
client-side vertex array path (`glVertexPointer`/`glNormalPointer`/etc.).
VAO support in OSG is conditional on `GL_ARB_vertex_array_object` or a
GLES2/GLES3 profile; neither is configured here, so OSG does **not** use VAOs.

**OSG_GLES3_AVAILABLE:** This CMake option does **not appear** in
`source/buildscripts/CMakeLists.txt` and was not found in the OSG fork's
`CMakeLists.txt` at commit 69cfeceb (search returned no matches). The OSG
fork used here does not expose `OSG_GLES3_AVAILABLE`; the GLES3 code path
is absent from the configured option set.

---

### E. ObjectPaging and mPagedRefs

**ObjectPaging only attached to the terrain quadtree** —
`apps/openmw/mwrender/renderingmanager.cpp`, `getWorldspaceChunkMgr()`:

```cpp
if (Settings::terrain().mObjectPaging)
{
    newChunkMgr.mObjectPaging =
        std::make_unique<ObjectPaging>(mResourceSystem->getSceneManager(), worldspace);
    quadTreeWorld->addChunkManager(newChunkMgr.mObjectPaging.get());
    mResourceSystem->addResourceManager(newChunkMgr.mObjectPaging.get());
}
```

This block is inside the `if (distantTerrain || groundcover)` guard, which
only executes when `Terrain::QuadTreeWorld` is created — i.e., only when
distant terrain is enabled. Confirmed: ObjectPaging is only attached to the
terrain quadtree.

**Active-grid skips non-STAT records** — `could not confirm` with an exact
line number. The file `components/terrain/objectpaging.cpp` returned HTTP 404
at the GitHub raw API for the tarball path. The design is architecturally
consistent with the description (only static objects are paged) and was
confirmed by context analysis.

**mPagedRefs — avoids creating separate render objects**
(`apps/openmw/mwworld/scene.cpp`):

```cpp
// A null sentinel node distinguishes paged objects from normal scene objects.
static osg::ref_ptr<SceneUtil::PositionAttitudeTransform> pagedNode =
    new SceneUtil::PositionAttitudeTransform;

void addObject(const MWWorld::Ptr& ptr, ...,
               const std::vector<ESM::RefNum>& pagedRefs, ...)
{
    ...
    if (!refnum.hasContentFile() ||
        !std::binary_search(pagedRefs.begin(), pagedRefs.end(), refnum))
        ptr.getClass().insertObjectRendering(ptr, model, rendering);
    else
        ptr.getRefData().setBaseNode(pagedNode);  // no render node created
    ...
}
bool Scene::isPagedRef(const Ptr& ptr) const
{
    return ptr.getRefData().getBaseNode() == pagedNode.get();
}
```

Objects whose RefNum appears in `mPagedRefs` get the shared `pagedNode`
sentinel instead of a real render node — confirmed they produce no separate
render object. `mPagedRefs` is populated from
`mRendering.getPagedRefnums(newGrid, mPagedRefs)` on each cell-grid change.

---

### F. Groundcover: VertexAttribDivisor on attributes 6 and 7, setNumInstances, no gl_InstanceID

**VertexAttribDivisor on attributes 6 and 7** —
`apps/openmw/mwrender/groundcover.cpp`:

```cpp
mStateset->setAttribute(new osg::VertexAttribDivisor(6, 1));
mStateset->setAttribute(new osg::VertexAttribDivisor(7, 1));
mProgramTemplate->addBindAttribLocation("aOffset",   6);
mProgramTemplate->addBindAttribLocation("aRotation", 7);
```

**setNumInstances** — `InstancingVisitor::apply(osg::Geometry&)` in the same
file:

```cpp
geom.getPrimitiveSet(i)->setNumInstances(static_cast<int>(mInstances.size()));
...
geom.setVertexAttribArray(6, transforms.get(), osg::Array::BIND_PER_VERTEX);
geom.setVertexAttribArray(7, rotations.get(),  osg::Array::BIND_PER_VERTEX);
```

**gl_InstanceID not used** — confirmed. `files/shaders/compatibility/groundcover.vert`
reads per-instance data exclusively from `attribute vec4 aOffset` (attrib 6)
and `attribute vec3 aRotation` (attrib 7). A GitHub search for `gl_InstanceID`
in that file returned no matches. This is consistent with the GL4ES emulation
path: GL4ES advances divisor attributes by calling `gles_glVertexAttrib4fv`
per instance inside the `fpe_glDrawArraysInstanced` loop, so the shader does
not need `gl_InstanceID`.

---

### G. Shader inventory

All shaders in `files/shaders/compatibility/` at OpenMW commit f4bec41,
after the android-runtime-baseline patcher has been applied. Counts are
per-file occurrences of each token in the **patched** source.

`†NM` = the raw `gl_NormalMatrix` reference was replaced by `normalToView()`
after the normal-transform patch (`apply-android-runtime-baseline.py`).

| Shader file | #version | gl_MVM | gl_PM | gl_NM | gl_TM | gl_FM | gl_LS | ftrans | tex2D | s2DProj | gl_FD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `objects.vert` | 120 | 1 | 0 | 1†NM | 1+ | 0 | 0 | 0 | 0 | 0 | 0 |
| `objects.frag` | 120 | 0 | 0 | 1†NM | 0 | 0 | 0 | 0 | 5+ | 0 | 5+ |
| `terrain.vert` | 120 | 0 | 0 | 1†NM | 1+ | 0 | 0 | 0 | 0 | 0 | 0 |
| `terrain.frag` | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3+ | 0 | 1+ |
| `groundcover.vert` | 120 | 1 | 0 | 1 | 1+ | 1 | 1 | 0 | 0 | 0 | 0 |
| `water.vert` | 120 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `water.frag` | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5+ | 0 | 1+ |
| `shadowcasting.vert` | 120 | 1 | 0 | 0 | 1+ | 0 | 0 | 0 | 0 | 0 | 0 |
| `shadows_fragment.glsl` | none | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `shadows_vertex.glsl` | none | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fog.glsl` | none | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fullscreen_tri.vert` | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `debug.vert` | 120 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `debug.frag` | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |

**Column key:**  
gl_MVM = `gl_ModelViewMatrix`, gl_PM = `gl_ProjectionMatrix`,
gl_NM = `gl_NormalMatrix`, gl_TM = `gl_TextureMatrix`,
gl_FM = `gl_FrontMaterial`, gl_LS = `gl_LightSource`,
ftrans = `ftransform`, tex2D = `texture2D`,
s2DProj = `shadow2DProj`, gl_FD = `gl_FragData`.

**Notes:**
- `gl_FrontMaterial` and `gl_LightSource` in `groundcover.vert` come from the
  `doLighting()` call inside the included `lib/light/lighting.glsl`.
- After `apply-android-runtime-baseline.py`, `fog.glsl` replaces all `gl_Fog.*`
  references with `OPENMW_FOG_*` macros; no raw `gl_Fog` remains in the patched
  file.
- `gl_TextureMatrix[N]` is used via `@diffuseMapUV` etc. macros; exact count
  varies with define combination.
- `†NM`: the raw `gl_NormalMatrix * passNormal` call was replaced by
  `normalToView()` by the normal-transform patch; the token still appears once
  in the GLSL `normalToViewMatrix = gl_NormalMatrix;` assignment.

---

## 7. Step 6 — Baseline Tables

Measurements have not been taken (no device access). The user must run the
procedure in `docs/perf/DEVICE_RUNBOOK.md` and populate these tables.

### S1 — Seyda Neen exterior, no shadows

| Run | Median frame ms | 1% low ms | Update ms | Cull ms | Draw ms | Real draws | Inst iters | Prog sw |
|---|---|---|---|---|---|---|---|---|
| Run 1 | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** |
| Run 2 | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** |
| Run 3 | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** |

### S2 — Balmora exterior, south bridge, no shadows — TODO

### S3 — Dense interior — TODO

### S4 — Balmora exterior with shadows (note quality setting) — TODO

### S5 — Groundcover exterior (optional) — TODO

### Stock vs perf-instrumented comparison (S1)

| Build | Median frame ms | δ vs stock |
|---|---|---|
| openmw-stock.apk | **TODO** | — |
| openmw-perf.apk (OPENMW_PERF_LOG unset) | **TODO** | **TODO** |

The two values should be within run-to-run thermal noise (typically ±2 ms).

---

## 8. Discrepancies and Blockers

### 8.1 Discrepancies between prompt and source

| ID | Item | Prompt says | Actual |
|---|---|---|---|
| D1 | `objectpaging.cpp` near line 748 | "active grid skips every record type except STAT" | Could not confirm: `components/terrain/objectpaging.cpp` returned HTTP 404 at the GitHub raw API for the pinned commit tarball path. Design is architecturally consistent. |
| D2 | `renderingmanager.cpp` near line 1238 | "ObjectPaging only attached to terrain quadtree" | Confirmed by symbol search; exact line 1238 not independently verified from raw URL. |
| D3 | `scene.cpp` near line 1035 | "how Scene uses mPagedRefs" | Confirmed conceptually; `pagedNode` sentinel mechanism confirmed in source; exact line 1035 not independently verified. |
| D4 | `OSG_GLES3_AVAILABLE` | "which CMake options does this OSG fork offer for GLES3" | No `OSG_GLES3_AVAILABLE` found in the OSG fork CMakeLists.txt at commit 69cfeceb. The fork does not expose this option. |

### 8.2 Blockers

| ID | Blocker | Impact |
|---|---|---|
| B1 | WSL has no installed distributions on this machine | Native build not executed; APK sha256 and file size are TODO; no APKs produced |
| B2 | No device access | GL environment strings not measured; all baseline tables are TODO |
| B3 | Gradle release gate validates a specific sha256 of `libopenmw.so` | The instrumented binary will not match `openmw-051-patch39-libopenmw.sha256`. Use `assembleDebug` or temporarily disable the sha256 check. |

### 8.3 Rollback procedure

```bash
git checkout source/buildscripts/CMakeLists.txt
cd source/buildscripts
./clean.sh --build
./build.sh --arch arm64 --lto --ccache
```

No app-side or settings files are changed by this phase.
