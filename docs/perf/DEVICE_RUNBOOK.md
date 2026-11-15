# Phase 1 Device Runbook — AYN Thor (Adreno 740)

This document gives exact step-by-step instructions for running the Phase 1
baseline on the AYN Thor.  Complete each numbered section in order.

---

## 0. Before you start

**Two APKs need to be built from a Linux machine first** (WSL with Ubuntu is
fine; the build host used for development has no WSL, so the user must do
this):

```bash
# Clone the branch on a Linux machine:
git clone https://github.com/tamdev999/OpenMW-Android.git -b perf/phase1-baseline
cd OpenMW-Android/source/buildscripts

# --- Perf-instrumented build ---
./build.sh --arch arm64 --lto --ccache
cd ..
./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk ~/openmw-perf.apk
sha256sum ~/openmw-perf.apk

# --- Stock build (no instrumentation) ---
# Edit CMakeLists.txt: comment out the two perf-patcher lines:
#   patches/gl4es/apply-perf-counters.py
#   patches/openmw051-final/apply-android-perf-log.py
./clean.sh --build
./build.sh --arch arm64 --lto --ccache
cd ..
./gradlew assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk ~/openmw-stock.apk
sha256sum ~/openmw-stock.apk
# Restore: git checkout source/buildscripts/CMakeLists.txt
```

Record the sha256 of each `libopenmw.so` and each APK in PHASE1_REPORT.md.

---

## 1. Backup saves and config

Installing a differently-signed APK requires uninstalling the existing one
first.  **Back up everything before uninstalling.**

```bash
# On the host, with adb connected:
adb shell "run-as org.libsdl.app tar -czf /sdcard/openmw_backup_$(date +%Y%m%d).tar.gz \
    /data/data/org.libsdl.app/files/"
adb pull /sdcard/openmw_backup_$(date +%Y%m%d).tar.gz .
```

If `run-as` fails (device not in developer mode), use the launcher's own
backup option, or copy the OpenMW user directory manually:
```bash
adb pull "/sdcard/Android/data/com.ast.openmw/files/OpenMW/" ./openmw_userdata_backup/
```

---

## 2. Create the perf output directory

```bash
adb shell mkdir -p \
    "/sdcard/Android/data/com.ast.openmw/files/OpenMW/perf"
```

This path is writable under Android scoped storage because it is inside the
app's own external files directory:

```
/storage/emulated/0/Android/data/com.ast.openmw/files/OpenMW/
```

This is `Constants.USER_FILE_STORAGE` in the app
(`MyApp.kt:setupPaths()` → `getExternalFilesDir(null)` + `"OpenMW"`).

---

## 3. Install the perf APK

```bash
adb uninstall org.libsdl.app          # required if a different signing key
adb install openmw-perf.apk
```

Restore your save backup after the first launch if needed:
```bash
adb push ./openmw_userdata_backup/ \
         "/sdcard/Android/data/com.ast.openmw/files/OpenMW/"
```

---

## 4. Device preparation (before every set of runs)

1. Connect the Thor to its charger.
2. Enable Sustained Performance Mode if available
   (Settings → Developer options → Sustained Performance Mode).
3. Confirm the device is cool: 5-minute idle from last run or reboot.
4. Set screen resolution in the launcher to your chosen render resolution
   and note it. Use the same resolution for every run.

---

## 5. Launcher settings

Open the OpenMW launcher before each scene and apply these settings:

### 5a. For scenes S1, S2, S3 (no shadows)

| Setting | Value |
|---|---|
| Frame Rate Limit | set to `0` via Custom Environment Flags (see §6) |
| Shadows | **Off** (disable in Visuals → Shadows) |
| Post-Processing / Shader Preset | note current preset; keep it the same across all runs |

### 5b. For scene S4 (shadows on)

Same as above, but:

| Setting | Value |
|---|---|
| Shadows | **On** |
| Shadow Quality | Medium (default) — note the exact setting |
| Shadow Distance | Medium (default) — note the exact setting |

### 5c. For scene S5 (groundcover, optional)

Enable Groundcover in Engine settings. Note mod name and density.

---

## 6. Custom Environment Flags — exact text to paste

In the launcher: **Advanced → Custom Environment Flags**

Replace `S1` with the scene name for each run (S1, S2, S3, S4, S5):

```
OPENMW_PERF_LOG=/sdcard/Android/data/com.ast.openmw/files/OpenMW/perf/ framerate_limit=0
```

This sets:
- `OPENMW_PERF_LOG` to the perf directory — the engine will create
  `perf-<timestamp>.csv` automatically for each launch, so runs never
  overwrite each other.
- `framerate_limit=0` — disables the 60-fps cap set by the launcher.

**Note:** `framerate_limit` written here via envLine takes effect only as an
engine environment variable. To also disable the launcher-written value in
`defaults.bin`, set the Frame Rate Limit spinner in the launcher to its
lowest value (20) or edit `settings.cfg` directly:
```ini
[Video]
framerate limit = 0
```

If vsync is still active on the display (120 Hz on the Thor), raw FPS may
still be capped. In that case, **use cull_ms, draw_ms, and real_draws as the
primary metrics** rather than FPS.

---

## 7. Scene list and save-file preparation

Create one save file per scene with the camera untouched after saving.
Stand still; do not move the camera during recording.

| Scene ID | Location | Shadow setting |
|---|---|---|
| **S1** | Seyda Neen exterior, facing town (south gate area) | Off |
| **S2** | Balmora exterior, south bridge, camera looking north | Off |
| **S3** | Dense interior: Vivec canton waistworks or a guild hall | Off |
| **S4** | Same position as S2 | **On** (default launcher quality) |
| **S5** | Grass-heavy exterior (only with a groundcover mod installed) | Off |

---

## 8. Per-scene run procedure

For each scene, perform **three runs** with a 5-minute cool-down between them.

```
1. Set OPENMW_PERF_LOG and launcher settings for this scene (§5, §6).
2. Launch the game.
3. Load the save for this scene.
4. Stand still, do not touch the camera.
5. Wait 10 seconds (ramp-up; these frames will be discarded in analysis).
6. Continue standing still for 60 seconds (the recording window).
7. Exit cleanly: main menu → Quit.
8. Cool-down: wait 5 minutes before the next run.
```

The CSV files appear automatically in:
```
/sdcard/Android/data/com.ast.openmw/files/OpenMW/perf/
```

Each run produces two files:
```
perf-<timestamp>.csv        ← frame data
perf-<timestamp>.csv.env.txt ← GL environment + engine config
```

---

## 9. Copy files back to the host

```bash
adb pull \
    "/sdcard/Android/data/com.ast.openmw/files/OpenMW/perf/" \
    ./docs/perf/raw/
```

Rename files to match the scene and run:
```
docs/perf/raw/S1_run1.csv
docs/perf/raw/S1_run1.csv.env.txt
docs/perf/raw/S1_run2.csv
...
```

---

## 10. Repeat with the stock APK

Install `openmw-stock.apk` using the same procedure as §3.
Run only scene S1, three times, with `OPENMW_PERF_LOG` **unset** (leave
Custom Environment Flags blank except for `framerate_limit=0`).
This gives the stock baseline to compare against the perf APK in §11.

---

## 11. Analysis

```python
import pandas as pd, numpy as np, sys

def summarise(path):
    df = pd.read_csv(path, comment='#')
    df = df.iloc[600:]          # discard first ~10 s at ~60 fps
    print(f"\n--- {path} ---")
    for col in ['frame_ms','update_ms','cull_ms','draw_ms',
                'real_draws','inst_iters','prog_sw']:
        med = df[col].median()
        pct99 = df[col].quantile(0.99)   # 99th pct = 1% low for frame_ms
        print(f"  {col:14s}  median={med:.1f}  99pct={pct99:.1f}")

for f in sys.argv[1:]:
    summarise(f)
```

Usage:
```bash
python3 analyse.py docs/perf/raw/S1_run*.csv
```

Fill in PHASE1_REPORT.md §7 tables from the output.

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `OPENMW_PERF_LOG: could not open ...` in openmw.log | The perf directory does not exist. Run the `adb shell mkdir` command in §2. |
| No CSV produced | Check openmw.log for the `OPENMW_PERF_LOG:` line; confirm envLine was parsed (check for `=` in each token). |
| `.env.txt` missing | The sidecar is written on the first frame. If the game crashed before frame 1, there will be no .env.txt. |
| APK install fails with signature mismatch | Run `adb uninstall org.libsdl.app` first, then install. Back up saves first (§1). |
| Frame cap still active after `framerate_limit=0` | The display driver may enforce vsync at 120 Hz. Use cull_ms and draw_ms as primary metrics. |
| Thermal throttling between runs | Extend cool-down to 10 minutes; use a fan or remove the case. |
