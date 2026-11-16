# Render Scale + SGSR1 Upscaling — Implementation Report

**Branch:** `feat/render-scale-sgsr1`  
**Base:** `perf/phase1-baseline` (OpenMW 0.51.0 Final, GL4ES GLES2 path)  
**libopenmw.so sha256 (stripped, no LTO):** `8bec8e0876b471ee5e414c451d750e62c78ebfd71f7e54fa425fc27062fb482a`  
**APK sha256:** `b80ebe633338a51be1279a0a647d8251a5add771e2253f798e2d72e8f8e4641c`  
**APK path:** `app/build/outputs/apk/mainline/debug/app-mainline-debug.apk`

---

## 1. Files changed

| File | Change |
|---|---|
| `apps/openmw/mwrender/postprocessor.hpp` | New private members `mRenderScale`, `mScaled`, `mUpscaler`, `mSharpness`; new public accessors `isRenderScaled()`, `getUpscaler()`, `getSharpness()` |
| `apps/openmw/mwrender/postprocessor.cpp` | Read env vars in constructor; scaled `renderWidth()`/`renderHeight()`; fixed `setResolution()` in `cull()`; pass scale info to canvas |
| `apps/openmw/mwrender/pingpongcull.cpp` | Create `mViewportStateset`/`mViewport` when stereo **or** `pp->isRenderScaled()` |
| `apps/openmw/mwrender/pingpongcanvas.hpp` | New members `mRenderScaled`, `mUpscaler`, `mSharpness`, `mSgsrViewportInfo`, `mSgsrEdgeSharpness`, `mSgsrStateSet`; new setters |
| `apps/openmw/mwrender/pingpongcanvas.cpp` | SGSR1 vertex+fragment shader strings; SGSR state set construction; scaled no-PP path; scaled after-loop upscale pass; prevent direct-to-screen resolve when scaled |
| `source/buildscripts/patches/openmw051-final/0100-render-scale-sgsr1.patch` | Deliverable patch, 354 lines |
| `source/buildscripts/CMakeLists.txt` | `0100-render-scale-sgsr1.patch` appended to `OPENMW_PATCH` |
| `source/app/src/main/java/ui/activity/GameActivity.kt` | Sets `OPENMW_RENDER_SCALE` and `OPENMW_SGSR_SHARPNESS` from prefs before the `envLine` block |
| `source/app/src/main/res/xml/settings.xml` | Added `gs_render_scale` and `gs_sgsr_sharpness` list preferences to `category_performance` |
| `source/app/src/main/res/values/strings.xml` | English strings and entry arrays for both new preferences |
| `source/app/src/main/res/values-{de,fr,pl,ru,sv}/strings.xml` | English fallback strings for both new keys (arrays not needed in locale files) |
| `source/3rdparty-licenses.txt` | Qualcomm SGSR1 BSD-3-Clause licence |

All engine changes are delivered as `0100-render-scale-sgsr1.patch`. No extracted source was edited to implement features; two post-compile fixes were applied and immediately folded back into the patch via regeneration.

---

## 2. How the feature works

### 2.1 Switches (read once at startup in `PostProcessor` constructor)

| Env var | Range | Default | Meaning |
|---|---|---|---|
| `OPENMW_RENDER_SCALE` | `[0.5, 1.0]` | `1.0` | Scene render fraction |
| `OPENMW_SCENE_UPSCALER` | `sgsr1` / `bilinear` | `sgsr1` | Upscale algorithm |
| `OPENMW_SGSR_SHARPNESS` | `[1.0, 2.0]` | `2.0` | SGSR edge sharpness |

`scaled` flag: `(mRenderScale < 0.999) && !Stereo::getStereo()`.  
When `scaled` is false, **no new branch is taken anywhere.** Behaviour is byte-for-byte identical to the base.

### 2.2 Scaled render sizes (`postprocessor.cpp`)

```cpp
int PostProcessor::renderWidth() const {
    if (Stereo::getStereo()) return stereo eye width;
    if (mScaled)
        return std::max(2, (int(mWidth * mRenderScale + 0.5f)) & ~1);
    return mWidth;
}
```

`createObjectsForFrame()` already sizes every scene texture and FBO from `renderWidth()`/`renderHeight()`. Confirmed at lines 461–462 of patched `postprocessor.cpp`. **No change needed there.**

### 2.3 Scene viewport (`pingpongcull.cpp`)

`mViewportStateset` and `mViewport` are now created when **stereo OR scaled**. The existing block:

```cpp
if (mViewportStateset) {
    mViewport->setViewport(0, 0, mPostProcessor->renderWidth(), mPostProcessor->renderHeight());
    renderStage->setViewport(mViewport);
    cv->pushStateSet(mViewportStateset.get());
    traverse(node, cv);
    cv->popStateSet();
}
```

already constrains the scene traversal to the reduced size. **Not duplicated.**

### 2.4 Effect resolution (`postprocessor.cpp cull()`)

When scaled, `setResolution()` receives `(renderWidth(), renderHeight())` instead of the window viewport size. Effects see `omw.resolution` as the scene size, not the window size. Quarter-size bloom and other scaled passes continue to work because their viewports are stored in `pass.mStateSet` (line 679 of patched `postprocessor.cpp`) and applied after `mRenderViewport`, overriding it correctly. This was verified by reading `dirtyTechniques()`.

### 2.5 Canvas draw path (`pingpongcanvas.cpp`)

**`mRenderViewport`** is now created when stereo **or** `mRenderScaled`. The pass loop already applies it at the start of each pass.

**No-PP fallback branch** (`filtered.empty() || !mPostprocessing`): when scaled, an SGSR (or bilinear) draw is inserted before the existing Android gamma path, using the scene texture at the reduced size and outputting to the full-screen viewport. The existing path is not taken when scaled.

**`pass.mResolve && index == filtered.back()` branch**: when `mRenderScaled`, this branch is skipped — the last pass writes into a scene-sized ping-pong FBO via the `lastPass` branch instead. After the loop, a final SGSR/bilinear draw upscales the result to `resolveViewport`.

**Android gamma (`mAndroidGammaStateSet`)**: The existing `androidGammaPending` mechanism redirects the final pass to a ping-pong FBO first, then applies gamma after the loop. The new SGSR draw fires before the Stereo multiview block, independent of gamma. When `OPENMW_GAMMA` is set alongside `OPENMW_RENDER_SCALE`, gamma applies after the SGSR upscale — this is the correct order.

### 2.6 SGSR1 shader

The vertex shader outputs `uv = gl_Position.xy * 0.5 + 0.5` — consistent with the triangle vertices `(-1,-1), (-1,3), (3,-1)` used by the canvas. This was confirmed by reading the existing gamma shader in the patched canvas (`uv = (gl_Position.xy * 0.5 + 0.5) * scaling`; the SGSR vert omits `scaling` since it always fills the destination exactly).

The fragment shader is Qualcomm's SGSR1 `sgsr1_shader_mobile.frag` RGBA mode, ported to GLSL 1.20 by replacing each `textureGather` with four `texture2D` fetches at exact texel centres in gather order (x=i0,j1; y=i1,j1; z=i1,j0; w=i0,j0). `std` renamed to `std2` to avoid clash with the C++ `std` namespace (not applicable in GLSL but kept for clarity). Edge threshold `8.0/255.0` matches the reference.

**`glslangValidator` is not available on the build host.** Both shaders were reviewed manually for GLSL 1.20 correctness: no `in`/`out` qualifiers, no `texture()` (uses `texture2D()`), no default uniform initialisers, no GLSL 3.x built-ins.

The scene textures use `LINEAR` / `CLAMP_TO_EDGE` (confirmed in `createObjectsForFrame()` lines 476–480 of patched source). The ping-pong FBO textures in the canvas are created by `attachCloneOfTemplate()` which does `SHALLOW_COPY` of the scene texture, inheriting its filter and wrap settings. **Correct for SGSR.**

### 2.7 Launcher (Stage 3)

`GameActivity.loadLibraries()` sets `OPENMW_RENDER_SCALE` and `OPENMW_SGSR_SHARPNESS` **before** the `envLine` block so users can override with `OPENMW_SCENE_UPSCALER=bilinear` etc. in custom environment flags. The existing gated `Os.setenv` lines for `OPENMW_GLES_VERSION` and `LIBGL_ES` are **not touched**.

---

## 3. Discrepancies between this prompt and the actual source

| Item | Prompt says | Source had |
|---|---|---|
| `renderWidth()` / `renderHeight()` description | "Today they return the stereo eye size or mWidth/mHeight" | Confirmed exactly. |
| `PingPongCull` constructor | "Create them when stereo OR postProcessor->isRenderScaled()" | Stereo-only gate confirmed. Added the scaled condition. |
| `PingPongCull::operator()` | "mViewport->setViewport(0,0,renderWidth,renderHeight); renderStage->setViewport(mViewport); push stateset; traverse; pop" | Confirmed exactly. The existing block was not duplicated. |
| `PostProcessor::cull()` setResolution | "pass (renderWidth(), renderHeight()) instead" | Current code uses `cv->getViewport()->width/height()`. Fixed. |
| `PingPongCanvas` `mRenderViewport` creation | "inside the handle==0 or mDirty block" | Confirmed at lines 215–220 of patched canvas. |
| Per-pass viewport for custom render targets | "find how passes with their own sized render target get their viewport" | `subPass.mStateSet->setAttributeAndModes(new osg::Viewport(0, 0, w, h))` at `postprocessor.cpp:679`. Applied to the pass state set, pushed **after** `mRenderViewport->apply()` in the draw loop, so custom-sized passes correctly override `mRenderViewport`. |
| Android gamma | "Check what port's Android edits do" | `mAndroidGammaStateSet`: built in constructor; in no-PP branch intercepts and returns; in pass loop redirects last pass via `androidGammaPending`; fires after loop. SGSR coexists correctly. |
| SGSR vert `uv` convention | "uv = gl_Vertex.xy * 0.5 + 0.5 unless that file does something different" | Gamma shader uses `gl_Position.xy * 0.5 + 0.5`. Used the same. |
| `glslangValidator` | "If not available, say so" | **Not available on this WSL host.** Shaders reviewed manually. |

---

## 4. Things that must still work — verification

| Feature | Verified how | Status |
|---|---|---|
| UI / HUD / menus / cursor | `mHUDCamera` viewport is set to `(0, 0, mWidth, mHeight)` in constructor and `resize()` — never reads `renderWidth()`/`renderHeight()`. MyGUI camera is a child of `mHUDCamera`. | ✓ Not affected |
| Effects with custom render targets (bloom, godrays) | `dirtyTechniques()` calls `renderTarget.mSize.get(renderWidth(), renderHeight())` — their quarter-size viewports are correct at the new scene size. Applied to `pass.mStateSet`, override `mRenderViewport` during draw. | ✓ Correct |
| Distortion and transparent-depth buffers | Sized in `createObjectsForFrame()` from `renderWidth()`/`renderHeight()`. Confirmed at lines 461–530 of patched `postprocessor.cpp`. | ✓ Correct |
| Screenshots, local map, water reflection/refraction, shadow maps | These use their own `osg::Camera` objects independent of the main camera viewport. `PostProcessor::cull()` does not resize any of these. | ✓ Not affected |
| Window resize / Android surface recreation | `PostProcessor::resize()` calls `createObjectsForFrame()` which calls `renderWidth()`/`renderHeight()` — the scaled size is recomputed from the new `mWidth`/`mHeight`. | ✓ Correct |
| Scale 1.0 | `mScaled = (mRenderScale < 0.999f) && !Stereo::getStereo()` — when unset or 1.0, `mScaled` is false and no new branch is taken. | ✓ Exact existing path |

---

## 5. Build fixes

One compile error was found and fixed (not a logic change):

**`pingpongcanvas.cpp:578`** — `static_cast<osg::Texture*>(getTexture())` rejected because `drawImplementation()` is `const` and `osg::FrameBufferAttachment::getTexture()` returns `const osg::Texture*`. Fixed by using a C-style cast `(osg::Texture*)`, consistent with the same pattern used throughout the existing canvas code (e.g., `attachCloneOfTemplate` at line 231).

A `sed` command used during development accidentally changed `static_cast<osg::Texture*>(tex->clone(...))` in `attachCloneOfTemplate` to `((osg::Texture*)()tex->clone(...))`. Fixed with a Python script. The final patch was regenerated from the corrected source tree and verified to apply cleanly to a fresh extract (`PATCH_CLEAN_APPLY: 0`).

---

## 6. Rollback

Set **Render Resolution** to `100% (Native)` in the launcher, or remove the patch line from `OPENMW_PATCH` in `CMakeLists.txt` and rebuild.

---

## 7. Device checklist

The following checks must be performed on the AYN Thor before this branch is considered ready.

**Before installing:** This is a debug APK signed with a different key than the release. Android will require an uninstall first.
```bash
adb pull "/sdcard/Android/data/com.ast.openmw/files/OpenMW/" ./openmw_backup_sgsr1/
adb uninstall org.libsdl.app
adb install app/build/outputs/apk/mainline/debug/app-mainline-debug.apk
```

---

**CHECK 1 — Scale 100% baseline.**
Set Render Resolution to 100% (Native) in the launcher. Play for a few minutes. The game must look and behave exactly as before this change. No visual differences, no crashes.

**CHECK 2 — Scale 75% fills the screen.**
Set Render Resolution to 75%. Load any exterior save (e.g., Seyda Neen). The world must fill the entire screen — no small image in a corner, no black borders, no stretched or cropped picture. The image may be softer than at 100% (expected).

**CHECK 3 — UI sharpness at 75%.**
With scale at 75%, open the inventory, map, and journal. UI text must be as sharp as at 100% — the HUD renders at full resolution independently of the scene.

**CHECK 4 — Effects alignment.**
With scale at 75%, verify:
- Water surface, reflections, and refractions line up with the world (no spatial offset).
- Shadow edges fall on the same objects they did at 100%.
- Bloom halo appears around bright objects at their actual screen positions.
- God-ray shafts point toward the sun and do not appear doubled or offset.
- Rain lens effect (if weather is rainy) covers the screen uniformly.

**CHECK 5 — SGSR vs bilinear comparison.**
Add `OPENMW_SCENE_UPSCALER=bilinear` to Custom Environment Flags in the launcher (Advanced settings). Load the same exterior. The world should look noticeably softer than the default. Remove the flag; the game should look sharper again. This confirms SGSR is active by default.

**CHECK 6 — Targeting and navigation.**
At 75% scale: aim the crosshair at an NPC and confirm the activation tooltip appears at the right object. Open the world map; the player marker must be in the correct location.

**CHECK 7 — All scale steps.**
Test each preset: 100%, 85%, 75%, 67%. All must produce a full-screen image with the same resolution-independent HUD. Performance should improve as scale decreases.

**CHECK 8 — SGSR sharpness presets.**
At 75% scale, switch through Soft / Medium / Sharp. Soft should produce a smoother result than Sharp on edges (most visible on foliage, NPC clothing edges, and signpost text).
