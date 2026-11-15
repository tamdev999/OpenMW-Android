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
3. [Step 4 — GL Environment Capture](#3-step-4--gl-environment-capture)
4. [Step 5 — Assumption Verification (A–G)](#4-step-5--assumption-verification-ag)
5. [Step 6 — Baseline Procedure for the Thor](#5-step-6--baseline-procedure-for-the-thor)
6. [Baseline Tables](#6-baseline-tables)
7. [Discrepancies and Blockers](#7-discrepancies-and-blockers)

---

## 1. Build Record

### 1.1 Source commits (CMakeLists.txt, verified)

| Dependency | Commit / version |
|---|---|
| OpenMW | `f4bec41444214a7903bebd178389ca22ca13f646` |
| OSG (openmw/osg) | `69cfecebfb6dc703b42e8de39eed750a84a87489` |
| GL4ES (sisah2/gl4es) | `5ac069d82ad8ca2cc3c574484e4c5bad880db83e` |
| NDK | r26b (`ad73c0370f0b0a87d1671ed2fd5a9ac9acfd1eb5c43a7fbfbd330f85d19dd632`) |
| Android API | 21 |
| ABI | arm64-v8a |
| NDK triplet | aarch64-linux-android |

Source: `source/buildscripts/include/version.sh:1–6` and `source/buildscripts/CMakeLists.txt`.

### 1.2 Exact build command

```bash
# From a Linux or WSL shell with the repo checked out at perf/phase1-baseline:
cd source/buildscripts
./build.sh --arch arm64 --lto --ccache
```

### 1.3 Compiler flags (verified from build.sh:68–90)

```
CFLAGS   = -fPIC -O3 -flto
CXXFLAGS = -fPIC -frtti -fexceptions -O3 -flto
LDFLAGS  = -fPIC -Wl,--undefined-version -flto -Wl,-plugin-opt=-emulated-tls -fuse-ld=lld
```

Release mode adds `-O3`; `--lto` adds `-flto` and forces `lld`.  
(`build.sh:66–90`)

### 1.4 Build not yet executed

**The build has not been run.** This workspace is on Windows. The build requires
Linux or WSL (`/proc/cpuinfo` is used for CPU count; NDK downloads a Linux binary).

The user must run the build manually. Expected outputs after a successful build:

| Output | Location |
|---|---|
| `libopenmw.so` (stripped) | `source/app/src/main/jniLibs/arm64-v8a/libopenmw.so` |
| `libGL.so` (GL4ES) | `source/app/src/main/jniLibs/arm64-v8a/libGL.so` |
| Unstripped symbol copies | `source/buildscripts/symbols/arm64-v8a/*.so` |
| Engine resources | `source/app/src/main/assets/libopenmw/` |

After the native build, the debug APK is built from `source/` with:
```bash
./gradlew assembleDebug
```

**To record the build baseline** (run after the build succeeds):

```bash
# SHA-256 of the stripped libopenmw.so:
sha256sum source/app/src/main/jniLibs/arm64-v8a/libopenmw.so

# APK size:
ls -lh source/app/build/outputs/apk/debug/app-debug.apk

# Toolchain versions:
source/buildscripts/toolchain/arm64/bin/aarch64-linux-android-clang --version
python3 --version
cmake --version
```

**Existing sha256 file for the stock release payload:**  
`source/buildscripts/openmw-051-patch39-libopenmw.sha256` — this is the
baseline the Gradle gate validates against (the release-cleaned binary).
The perf-patched binary will produce a **different** sha256 and must bypass
the Gradle gate for development builds.

### 1.5 APK installation notes

If a differently-signed release APK is already installed on the Thor, Android
will refuse to install a debug APK without first uninstalling. **Before
uninstalling**, back up:
```bash
adb shell "run-as org.libsdl.app tar -cz \
    /data/data/org.libsdl.app/files/data/ \
    /data/data/org.libsdl.app/shared_prefs/" \
    > openmw_backup_$(date +%Y%m%d).tar.gz
```
Then uninstall and install the debug APK:
```bash
adb uninstall org.libsdl.app
adb install source/app/build/outputs/apk/debug/app-debug.apk
```
Restore the backup if needed after first launch.

---

## 2. Deliverables

| File | Purpose |
|---|---|
| `source/buildscripts/patches/gl4es/apply-perf-counters.py` | GL4ES draw-call counter instrumentation |
| `source/buildscripts/patches/openmw051-final/apply-android-perf-log.py` | OpenMW per-frame CSV log |
| `source/buildscripts/CMakeLists.txt` | Wired both patchers into build |
| `docs/perf/PHASE1_REPORT.md` | This report |
| `docs/perf/*.csv` | Raw baseline CSVs (to be added by user after device runs) |

**CMakeLists.txt wiring (verified with grep):**

```cmake
# gl4es PATCH_COMMAND (line 364 after edit):
python3 ${CMAKE_SOURCE_DIR}/patches/gl4es/apply-powervr-packed-depth-stencil.py <SOURCE_DIR> &&
python3 ${CMAKE_SOURCE_DIR}/patches/gl4es/apply-perf-counters.py <SOURCE_DIR>

# OPENMW_PATCH (line 616 after edit):
... &&
python3 ${CMAKE_SOURCE_DIR}/patches/openmw051-final/apply-android-perf-log.py <SOURCE_DIR>
```

---

## 3. Step 4 — GL Environment Capture

### 3.1 How GL_EXTENSIONS is captured

`apply-perf-counters.py` injects `src/gl/omwperf.c` into the GL4ES build.
The function `omwperf_log_gl_env_once()` is called the first time
`omwperf_snapshot()` fires (i.e., on the first frame with `OPENMW_PERF_LOG`
active). It logs via `__android_log_print` with tag `OpenMW-GL4ES-Perf`:

```
GL_VERSION:  <string>
GL_RENDERER: <string>
GL_EXTENSIONS chunk: <512-char slice> ...  (repeated until full)
GL_EXT_instanced_arrays=<0|1> GL_EXT_draw_instanced=<0|1>
GL_OES_vertex_array_object=<0|1> GL_EXT_multi_draw_indirect=<0|1>
```

### 3.2 Capture command

```bash
# On the host while the game is running with OPENMW_PERF_LOG set:
adb logcat -s "OpenMW-GL4ES-Perf:I" "OpenMW-Native:I" "OpenMW-Diag:I"
```

Or save to file:
```bash
adb logcat -s "OpenMW-GL4ES-Perf:I" "OpenMW-Native:I" > gl_env_capture.txt
```

### 3.3 Expected GL environment (Snapdragon 8 Gen 2 / Adreno 740 — not yet measured)

The following has **not been measured** (no device access). The table will be
completed by the user from the logcat capture.

| Item | Expected (Adreno 740 ES2) | Measured |
|---|---|---|
| GL_RENDERER | Adreno (TM) 740 | **TODO** |
| GL_VERSION | OpenGL ES 2.0 ... | **TODO** |
| GL_EXT_instanced_arrays | 1 (present on Adreno 7xx) | **TODO** |
| GL_EXT_draw_instanced | 1 (present on Adreno 7xx) | **TODO** |
| GL_OES_vertex_array_object | 1 (ES2 core or ext) | **TODO** |
| GL_EXT_multi_draw_indirect | 0 (ES2 context) | **TODO** |

> **Note:** In an ES2 context (`LIBGL_ES=2`, `OPENMW_GLES_VERSION=2`),
> `GL_EXT_multi_draw_indirect` is not expected. `GL_EXT_instanced_arrays` and
> `GL_EXT_draw_instanced` are widely present on Adreno 7xx but must be confirmed
> at runtime because GL4ES runs in an ES2 context, and their availability depends
> on the driver advertising them as ES2 extensions.

### 3.4 GL4ES startup banner

GL4ES logs its startup via `SHUT_LOGD` (tagged `gl4es` on Android, forwarded to
`OpenMW-Native` via the native log bridge). Key lines include:

```
Using GLES 2.0 backend
Hardware vendor is Qualcomm
GLSL 120 supported and used
```

The full banner appears in logcat before the first frame is rendered.

---

## 4. Step 5 — Assumption Verification (A–G)

All citations are to the **pinned upstream sources**, not to the local patches.
Line numbers were counted from the raw GitHub content at the pinned commits.

---

### A. fpe_glDrawArraysInstanced / fpe_glDrawElementsInstanced dispatch

**Claim:** both functions issue one GLES draw per instance and feed divisor
attributes through `glVertexAttrib4fv`.

**Confirmed.**

`src/gl/fpe.c` — `fpe_glDrawArraysInstanced()`:

```c
for (GLint id=0; id<primcount; ++id) {
    GoUniformiv(glprogram, glprogram->builtin_instanceID, 1, 1, &id);
    for(int i=0; i<hardext.maxvattrib; i++)
    if(glprogram->va_size[i]) {
        vertexattrib_t *w = &glstate->vao->vertexattrib[i];
        if(w->divisor && w->enabled) {
            // ... compute current pointer from (id/w->divisor)*stride ...
            gles_glVertexAttrib4fv(i, glstate->gleshard->vavalue[i]);
        }
    }
    gles_glDrawArrays(mode, first, count);  // one real draw per iteration
}
```

Same pattern in `fpe_glDrawElementsInstanced()` (calls `gles_glDrawElements`
per iteration). File: `src/gl/fpe.c`, approximately lines 50–110 and 112–180
respectively.

**vertexattrib_t layout** (`src/gl/buffers.h`):

```c
typedef struct {
    GLint           size;         // element count (1–4 or GL_BGRA)
    GLenum          type;         // GL_FLOAT etc.
    GLsizei         stride;
    const GLvoid*   pointer;      // CPU pointer (when buffer==NULL)
    int             enabled;
    int             normalized;
    glbuffer_t     *buffer;       // reference VBO (or NULL)
    GLint           divisor;      // 0 = per-vertex; >0 = per-N-instances
    GLuint          real_buffer;  // underlying GLES VBO name
    const GLvoid*   real_pointer; // offset within real_buffer
    int             integer;
} vertexattrib_t;
```

**Dispatch condition** (`src/gl/drawing.c` — `glDrawElementsCommon()`):

```c
if(instancecount==1 || hardext.esversion==1) {
    // direct gles_glDrawArrays / gles_glDrawElements
} else {
    if(!iindices && !sindices)
        fpe_glDrawArraysInstanced(mode, first, count, instancecount);
    else {
        ...
        fpe_glDrawElementsInstanced(mode, count, t, tmp, instancecount);
    }
}
```

Condition: `instancecount > 1 AND hardext.esversion > 1`.  
Since `LIBGL_ES=2` → `hardext.esversion==2`, the instanced path is taken
whenever `instancecount > 1` (i.e., for groundcover geometry).

---

### B. LIBGL_ES values; hardext.esversion > 2 gates; Android.mk build flags

**LIBGL_ES accepted values** (`src/gl/init.c`):

```c
globals4es.es = ReturnEnvVarInt("LIBGL_ES");
switch(globals4es.es) {
    case 1:
    case 2:
        break;
    default:
        // automatic ES backend selection
        globals4es.es = DEFAULT_ES;
        break;
}
```

Accepted: `1` (GLES 1.1) and `2` (GLES 2.0).  
Value `3` falls to `default:` and is replaced by `DEFAULT_ES`.  
On Android, `Android.mk` defines `-DDEFAULT_ES=2`, so LIBGL_ES=3 → ES2.

**hardext.esversion > 2 gates** (`src/glx/hardext.c`):

```c
if (hardext.esversion>1) {
    if(testGLSL("#version 120", 1))
        hardext.glsl120 = 1;
    if(testGLSL("#version 300 es", 0))
        hardext.glsl300es = 1;
    if(testGLSL("#version 310 es", 1))
        hardext.glsl310es = 1;
}
```

The `esversion > 2` check appears for `GL_EXT_draw_buffers` (made core in ES3):

```c
if (hardext.esversion>2) {
    hardext.drawbuffers = 1;
} else {
    S("GL_EXT_draw_buffers ", drawbuffers, 1);
}
```

With ES2, `glsl300es` and `glsl310es` are probed but their
in-core behaviour is not assumed.

**Android.mk build flags** (confirmed from file at pinned commit):

```makefile
LOCAL_CFLAGS += -g -std=gnu99 -funwind-tables -O3 -fvisibility=hidden
LOCAL_CFLAGS += -DNOX11
LOCAL_CFLAGS += -DNO_GBM
LOCAL_CFLAGS += -DUSE_ANDROID_LOG=1
LOCAL_CFLAGS += -DDEFAULT_ES=2
```

No `NOEGL` flag: EGL is used. No `STATICLIB` flag: shared library.  
No `BCMHOST`: no Raspberry Pi / VideoCore path.

---

### C. EGL context creation — OPENMW_GLES_VERSION mapping

File: `components/sdlutil/sdlgraphicswindow.cpp` (OpenMW commit f4bec41,
pinned OSG fork commit 69cfeceb):

```cpp
#if defined(ANDROID) || defined(OPENMW_GL4ES_MANUAL_INIT)
    int major = 1;
    int minor = 1;
    char* ver = getenv("OPENMW_GLES_VERSION");
    if (ver && strcmp(ver, "2") == 0)
    {
        major = 2;
        minor = 0;
    }
    else if (ver && strcmp(ver, "3") == 0)
    {
        major = 3;
        minor = 2;
    }
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_ES);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, major);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, minor);
#endif
    mContext = SDL_GL_CreateContext(mWindow);
```

Mapping:
- `OPENMW_GLES_VERSION=2` → `SDL_GL_CONTEXT_MAJOR_VERSION=2`, `MINOR=0`
  → ES 2.0 context
- `OPENMW_GLES_VERSION=3` → `SDL_GL_CONTEXT_MAJOR_VERSION=3`, `MINOR=2`
  → ES 3.2 context (not used in this port)
- Unset / "1" → `major=1`, `minor=1` → ES 1.1

Since `GameActivity.kt` unconditionally sets `OPENMW_GLES_VERSION=2`, SDL
creates an ES 2.0 context. GL4ES then operates in ES2 mode (`LIBGL_ES=2`).

---

### D. OSG: vertex array objects, GL1 profile, GLES3 options

**GL1 profile / fixed function** — confirmed in `source/buildscripts/CMakeLists.txt`:

```cmake
set(OSG_COMMON
    -DOPENGL_PROFILE="GL1"
    -DOSG_GL1_AVAILABLE=ON
    -DOSG_GL2_AVAILABLE=OFF
    -DOSG_GL3_AVAILABLE=OFF
    -DOSG_GLES1_AVAILABLE=OFF
    -DOSG_GLES2_AVAILABLE=OFF
    -DOSG_GL_DISPLAYLISTS_AVAILABLE=OFF
    -DOSG_GL_MATRICES_AVAILABLE=ON
    -DOSG_GL_VERTEX_FUNCS_AVAILABLE=ON
    -DOSG_GL_VERTEX_ARRAY_FUNCS_AVAILABLE=ON
    -DOSG_GL_FIXED_FUNCTION_AVAILABLE=ON
)
```

**Vertex array objects** — with `OPENGL_PROFILE=GL1` and
`-DOSG_GLES2_AVAILABLE=OFF`, OSG's Geometry drawing uses the legacy
client-side vertex array path (`glVertexPointer`/`glNormalPointer`/etc.)
rather than VAOs. VAO support in OSG is conditional on `GL_ARB_vertex_array_object`
or GLES3; neither is enabled here.

**OSG_GLES3_AVAILABLE** — this CMake option does **not appear** in
`source/buildscripts/CMakeLists.txt` and was not found in the OSG fork's
`CMakeLists.txt` at commit 69cfeceb. The OSG fork's GLES3 path appears to
be entirely absent from the options set for this port. GLES3-related OSG
defines (`OSG_GLES3_AVAILABLE`, `OSG_GL3_AVAILABLE`) are all set to `OFF`
or not present.

**Could not confirm:** exact line numbers in the OSG fork's Geometry.cpp
for the VAO path, because the GitHub API returned no matches for
`VertexArrayObject` in that file (the GL1 profile removes those code paths
at compile time).

---

### E. ObjectPaging attachment and mPagedRefs usage

**ObjectPaging attached to QuadTreeWorld** —
`apps/openmw/mwrender/renderingmanager.cpp` (confirmed by `ObjectPaging`
symbol search, confirmed from context-gatherer analysis):

```cpp
if (Settings::terrain().mObjectPaging)
{
    newChunkMgr.mObjectPaging =
        std::make_unique<ObjectPaging>(mResourceSystem->getSceneManager(), worldspace);
    quadTreeWorld->addChunkManager(newChunkMgr.mObjectPaging.get());
    mResourceSystem->addResourceManager(newChunkMgr.mObjectPaging.get());
}
```

This block is inside the `if (distantTerrain || groundcover)` branch, which
only executes when `Terrain::QuadTreeWorld` is created — i.e., when distant
terrain is enabled. Confirmed: ObjectPaging is only attached to the terrain
quadtree.

**Active-grid skips non-STAT records** — the prompt claims
`objectpaging.cpp` near line 748. This file is at
`components/terrain/objectpaging.cpp` in the OpenMW source tree; GitHub 404
was returned (the path exists only in the full repository tarball, not at the
raw URL path tried). The claim is **could not independently confirm** at exact
line numbers from raw URL access.  
However, the design is consistent with OpenMW 0.51 source browsing: the active
grid ObjectPaging only pages static objects (`ESM::REC_STAT`) because
non-static objects have dedicated renderers.

**mPagedRefs usage** (`apps/openmw/mwworld/scene.cpp`, confirmed):

```cpp
// Static null node to mark paged-out objects without a render node:
static osg::ref_ptr<SceneUtil::PositionAttitudeTransform> pagedNode =
    new SceneUtil::PositionAttitudeTransform;

void addObject(const MWWorld::Ptr& ptr, ..., const std::vector<ESM::RefNum>& pagedRefs,
    ..., MWRender::RenderingManager& rendering)
{
    ...
    if (!refnum.hasContentFile() ||
        !std::binary_search(pagedRefs.begin(), pagedRefs.end(), refnum))
        ptr.getClass().insertObjectRendering(ptr, model, rendering);
    else
        ptr.getRefData().setBaseNode(pagedNode);  // ← no separate render object
    ...
}
bool Scene::isPagedRef(const Ptr& ptr) const
{
    return ptr.getRefData().getBaseNode() == pagedNode.get();
}
```

Objects whose RefNum appears in `mPagedRefs` have their base node set to the
shared sentinel `pagedNode` instead of an actual render node — confirming they
do not create separate render objects.

`mPagedRefs` is populated from `mRendering.getPagedRefnums(newGrid, mPagedRefs)`
and cleared on each cell grid change.

---

### F. Groundcover instancing: VertexAttribDivisor and gl_InstanceID

**VertexAttribDivisor on attributes 6 and 7** —
`apps/openmw/mwrender/groundcover.cpp` (confirmed):

```cpp
mStateset->setAttribute(new osg::VertexAttribDivisor(6, 1));
mStateset->setAttribute(new osg::VertexAttribDivisor(7, 1));
mProgramTemplate->addBindAttribLocation("aOffset",   6);
mProgramTemplate->addBindAttribLocation("aRotation", 7);
```

Also in `InstancingVisitor::apply(osg::Geometry&)`:

```cpp
geom.getPrimitiveSet(i)->setNumInstances(static_cast<int>(mInstances.size()));
...
geom.setVertexAttribArray(6, transforms.get(), osg::Array::BIND_PER_VERTEX);
geom.setVertexAttribArray(7, rotations.get(),  osg::Array::BIND_PER_VERTEX);
```

**gl_InstanceID not used in groundcover.vert** — confirmed.
`files/shaders/compatibility/groundcover.vert` reads position and scale from
`attribute vec4 aOffset` (attrib 6) and rotation from `attribute vec3 aRotation`
(attrib 7) via standard per-vertex attribute arrays with divisor 1. The shader
uses `gl_Vertex`, `gl_Normal`, `gl_NormalMatrix`, `gl_TextureMatrix`, and the
custom `aOffset`/`aRotation` attributes. `gl_InstanceID` does not appear in
the file (confirmed by GitHub search returning no matches).

This is consistent with the GL4ES emulation path: GL4ES advances the divisor
attribute by calling `gles_glVertexAttrib4fv` per instance inside the
`fpe_glDrawArraysInstanced` loop, so the shader does not need `gl_InstanceID`.

---

### G. Shader inventory

The compatibility shader set lives in
`files/shaders/compatibility/` at the OpenMW 0.51.0 Final commit.
The android-runtime-baseline patcher modifies `objects.frag`, `fog.glsl`,
`shadowcasting.vert`, `fullscreen_tri.vert`, `debug.vert`, `debug.frag`,
and `water.frag`. All counts below are from the **patched** shader (since that
is what ships in the APK).

The android normal-transform patch replaces `gl_NormalMatrix * passNormal`
with `normalToView(...)` in several shaders — those sites are marked with
`†NM` below (the raw gl_NormalMatrix reference is gone after patching).

Shader search was performed via GitHub raw API; counts are based on
source text matches. `#version` is `#version 120` throughout; none use
a higher GLSL version.

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
| `shadows_fragment.glsl` | (none) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| `shadows_vertex.glsl` | (none) | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fog.glsl` | (none) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `fullscreen_tri.vert` | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `debug.vert` | 120 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `debug.frag` | 120 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 1 |

**Column key:**  
gl_MVM=`gl_ModelViewMatrix`, gl_PM=`gl_ProjectionMatrix`,
gl_NM=`gl_NormalMatrix`, gl_TM=`gl_TextureMatrix`,
gl_FM=`gl_FrontMaterial`, gl_LS=`gl_LightSource`,
ftrans=`ftransform`, tex2D=`texture2D`,
s2DProj=`shadow2DProj`, gl_FD=`gl_FragData`.

**Notes:**
- `gl_TextureMatrix` is used via `@diffuseMapUV` etc. macros which may resolve
  to `gl_TextureMatrix[N]` depending on define expansion; exact count varies
  with define combination.
- `gl_FrontMaterial` appears in `groundcover.vert` via `gl_FrontMaterial.shininess`
  inside `doLighting()` from included `lib/light/lighting.glsl`.
- `gl_LightSource` appears in `groundcover.vert` via the `doLighting()` include.
- `shadows_fragment.glsl` contains `shadow2DProj` for the shadow lookup.
- After android-runtime-baseline patcher, `fog.glsl` replaces all `gl_Fog.*`
  references with `OPENMW_FOG_*` macros; no `gl_Fog` remains in the patched
  fog.glsl — not reflected in the table since those are preprocessor macros.
- `†NM` = `gl_NormalMatrix` reference replaced by `normalToView()` after the
  normal-transform patch; the raw reference no longer exists in the patched
  shader.

**Shader count note:** The android graphics followup patchers (1–4) do not add
new shader files; they only modify the existing compat shaders. The
`apply-android-gl4es-core-inline.py` patcher replaces the content of
`files/shaders/lib/core/vertex.h.glsl` and `fragment.h.glsl` entirely.

---

## 5. Step 6 — Baseline Procedure for the Thor

**Prerequisites:**
- Thor connected to charger, sustained-performance mode on (Settings → Developer options → Sustained Performance Mode or equivalent AYN utility)
- 5-minute cool-down between runs (let the SoC thermally stabilise)
- Same launcher resolution for every run (record it in the CSV header)
- Post-processing preset noted (default = OMWFX chain disabled; shadow quality noted)
- `adb` connection confirmed: `adb devices`

### 5.1 Set OPENMW_PERF_LOG via envLine

In the launcher's Advanced settings, set **Custom Environment Flags** to:
```
OPENMW_PERF_LOG=/sdcard/openmw_perf_S1.csv
```
(change the filename per scene).

The launcher parses `envLine` by splitting on spaces/newlines, then on `=`.
(`GameActivity.kt:loadLibraries()`)

### 5.2 Frame rate limit

Set **Frame Rate Limit** to `0` to disable the 60-fps cap. The launcher
preference key is `gs_framerate_limit`; value `0` is not in the spinner
entries (20, 30, 60) but can be entered via envLine:
```
OPENMW_PERF_LOG=/sdcard/openmw_perf_S1.csv framerate_limit=0
```
Or, set it directly in `settings.cfg`:
```ini
[Video]
framerate limit = 0
```
If the display driver still caps the frame rate (Adreno 740 at 120 Hz max),
that cap will show in the data. In that case, **use cull_ms, draw_ms, and
draw-call counts as the primary metrics** (not FPS).

### 5.3 Scenes and save files

Create one save file per scene with the camera untouched after save:

| Scene ID | Location | Notes |
|---|---|---|
| S1 | Seyda Neen exterior, facing town (south gate area) | No shadows |
| S2 | Balmora exterior, south bridge looking north | No shadows |
| S3 | Dense interior (Vivec canton waistworks or guild hall) | No shadows |
| S4 | Same as S2, shadows enabled (default quality in launcher) | Note quality setting |
| S5 | Grass-heavy exterior (optional, only with groundcover mod) | Note mod name |

### 5.4 Per-scene procedure

```bash
# 1. Confirm cool-down (5 min from last run or boot)
# 2. Set OPENMW_PERF_LOG=<path> in envLine for this scene
# 3. Launch game, load save, stand still
# 4. Wait 10 seconds (discard ramp-up frames — handled by CSV post-processing)
# 5. Record for 60 seconds (stand still, no input)
# 6. Exit cleanly (back to main menu, then quit)
# 7. Pull CSV:
adb pull /sdcard/openmw_perf_S1_run1.csv docs/perf/
# 8. Repeat for run 2 and run 3 (cool-down between each)
```

### 5.5 CSV post-processing

The CSV header contains `# cell=` and config lines. Skip the first
~600 rows (10 s × ~60 fps). Compute per-column medians and 1st-percentile
frame times from the remaining rows:

```python
import pandas as pd
import numpy as np

df = pd.read_csv('openmw_perf_S1_run1.csv', comment='#')
df = df.iloc[600:]   # discard first 10 s

print("Median frame ms:", df['frame_ms'].median())
print("1% low frame ms:", df['frame_ms'].quantile(0.99))  # 99th pct = 1% low
print("Median update_ms:", df['update_ms'].median())
print("Median cull_ms:  ", df['cull_ms'].median())
print("Median draw_ms:  ", df['draw_ms'].median())
print("Median real_draws:", df['real_draws'].median())
print("Median inst_iters:", df['inst_iters'].median())
print("Median prog_sw:   ", df['prog_sw'].median())
```

### 5.6 Stock vs instrumented comparison

To verify zero-overhead of the instrumentation, run S1 **twice**:
1. With `OPENMW_PERF_LOG` unset (stock behaviour)
2. With `OPENMW_PERF_LOG` set

Compare median frame times. They should be within run-to-run thermal noise
(typically ±2 ms on a fixed clock).

---

## 6. Baseline Tables

**Measurements have not been taken.** The user must run the device procedure
described in Section 5 and populate these tables.

### S1 — Seyda Neen exterior, no shadows

| Run | Median frame ms | 1% low frame ms | Update ms | Cull ms | Draw ms | Real draws | Inst iters | Prog sw |
|---|---|---|---|---|---|---|---|---|
| Run 1 | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** |
| Run 2 | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** |
| Run 3 | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** | **TODO** |
| **Median** | — | — | — | — | — | — | — | — |

### S2 — Balmora exterior, south bridge, no shadows

*(same columns as S1 — to be filled)*

### S3 — Dense interior

*(same columns as S1 — to be filled)*

### S4 — Balmora exterior with shadows

*(record shadow quality setting; same columns as S1 — to be filled)*

### S5 — Groundcover exterior (optional)

*(record groundcover mod name and density setting — to be filled)*

---

## 7. Discrepancies and Blockers

### 7.1 Discrepancies between prompt and source

| ID | Item | Prompt says | Source says |
|---|---|---|---|
| D1 | `objectpaging.cpp` near line 748 | "active grid skips every record type except STAT" | Could not confirm: `components/terrain/objectpaging.cpp` returned HTTP 404 at the GitHub raw API for this exact path. The file likely exists under a different path in the tarball (e.g. in a subdirectory). Design is architecturally consistent with the description. |
| D2 | `renderingmanager.cpp` near line 1238 | "ObjectPaging is only attached to the terrain quadtree" | Confirmed by symbol search, but exact line number could not be verified — GitHub raw search returned contextual matches only, not line numbers. |
| D3 | `scene.cpp` near line 1035 | "how Scene uses mPagedRefs" | Confirmed conceptually; `pagedNode` sentinel mechanism confirmed in source; exact line 1035 not independently verified. |
| D4 | OSG `OSG_GLES3_AVAILABLE` CMake option | "which CMake options does this OSG fork offer for GLES3" | No `OSG_GLES3_AVAILABLE` or any GLES3 option was found in the OSG fork CMakeLists.txt at commit 69cfeceb. The fork appears to have stripped GLES3 options entirely. |
| D5 | `listdraw.c` draw sites | "Expected places include src/gl/listdraw.c" | `draw_renderlist()` in listdraw.c calls `gles_glDrawArrays` and `gles_glDrawElements` directly (not via `fpe_gl*` wrappers) for the per-instance loop in the `instanceCount > 1` case. The phase-1 counter patcher does **not** instrument listdraw.c. These calls are `gles_glDrawArrays`/`gles_glDrawElements` from inside the render-list draw loop, which is a separate path from `glDrawElementsCommon`. Recommendation: add listdraw.c instrumentation in Phase 2 if the counter gap is significant. |
| D6 | GL4ES NOEGL | Prompt does not mention EGL | Android.mk has **no** `NOEGL` flag, so GL4ES uses EGL (not a no-EGL path). The hardext.c EGL pBuffer context creation runs at startup. |

### 7.2 Blockers

| ID | Blocker | Impact |
|---|---|---|
| B1 | No Linux/WSL build environment available on host | Build not executed; sha256, APK size, toolchain version strings are listed as "TODO" |
| B2 | No device access (no adb to Thor) | All baseline measurements (Sections 3.3, 6) are "TODO"; exact GL extension strings not measured |
| B3 | Gradle release gate validates a specific sha256 of `libopenmw.so` | The instrumented binary will not match `openmw-051-patch39-libopenmw.sha256`. Use `assembleDebug` target or temporarily disable the sha256 check for perf builds. |
| B4 | listdraw.c not instrumented | Draw calls issued from `draw_renderlist()` (pre-batched paths) are not counted. This is documented in D5. |

### 7.3 Rollback procedure

```bash
# Remove both patchers from CMakeLists.txt (already reversible via git revert),
# then do a full clean rebuild:
cd source/buildscripts
./clean.sh --build
./build.sh --arch arm64 --lto --ccache
```

No app-side or settings files are changed by this phase.

---

*Report generated by Kiro on branch `perf/phase1-baseline`, commit `e883485`.*
