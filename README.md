# WireWalk Studio

A local desktop app for calibrating a rod from a screenshot, entering movement timestamps, and measuring the head's start-to-end separation along the rod. Distance and speed are exported as CSV files.

The app accepts arbitrary video filenames. It does **not** require a prebuilt calibration CSV, an Excel workbook, a particular naming convention, or a Codex installation. Videos and images are processed locally.

## Install and start

Use Python **3.12 or 3.13** with Tkinter.

On Windows, extract the ZIP and double-click **Start WireWalk Studio.cmd**. It creates a `.venv` folder and installs the packages on the first run. The launcher can use an available Codex Python runtime, but also works with a standard Python 3.12/3.13 installation and the Windows Python launcher.

Alternatively:

```sh
python -m venv .venv
# Windows:
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python wirewalk.py
# macOS/Linux:
# .venv/bin/python -m pip install -r requirements.txt
# .venv/bin/python wirewalk.py
```

On Linux, install your distribution's Tkinter package if needed. A video can also be opened with `python wirewalk.py --video "path/to/video.mp4"`.

## 1. Calibrate

1. Click **New video** and select a recording.
2. Optionally enter an animal ID, date (`YYYY-MM-DD`), and trial number for grouped summaries. The recording ID is used for export filenames. Spaces and filename punctuation are converted to underscores.
3. Enter the **physical length of the span you will select**. The default is 24 inches; centimeters and millimeters are also supported.
4. Click **Load screenshot** to choose a screenshot from this same recording. Alternatively, enter a reference video time, click **Show reference frame**, and **Use video frame as screenshot**.
5. Click the two endpoints of the known span in the left image. Use the mouse wheel or +/− to zoom; right-drag to pan; **Fit** restores the full image.
6. Click **Transfer to video**. The app matches scene features to map screenshot coordinates into original video coordinates, accounting for screenshot resizing and borders.
7. Check the green A/B endpoints in the right video preview. If the transfer fails or looks wrong, click **Correct endpoints in video** and select the same physical span directly in the right image.
8. Click **Confirm calibration**.

The scale is `known_length_cm / rod_length_in_video_pixels`. Rod tips and the points where the rod meets supports can represent different physical lengths: select the span whose length was actually measured. Selecting two points is manual; the app transfers those selected points but does not automatically identify the rod's physical tips.

One calibration is used for each recording. The camera must stay fixed within a recording. The two-point conversion assumes approximately uniform scale along the rod; substantial depth perspective needs additional calibration. Screenshots should come from the same camera/setup and recording.

## 2. Enter movement times

Enter intervals directly, using seconds or colon times:

```text
16-18
0:22-0:29
1:05.5-1:09
```

Commas, semicolons, and new lines separate intervals. Click **Apply intervals**. The app rejects overlapping intervals, reversed boundaries, and timestamps beyond the video. Fractions are allowed in the seconds component.

You can also inspect the video on this screen. Use the time box, seek slider, play/pause, or frame-step buttons, then **Set start**, **Set end**, and **Add interval**. The selected times are video timestamps, not computer playback time.

Or use **Import timestamp CSV**:

```csv
video,start_s,end_s
example_video.mp4,16,18
example_video.mp4,0:22,0:29
```

The `video` column is optional. If present, only rows whose video basename matches the current recording are imported. A single CSV can list many videos; files sharing the same basename should use separate CSVs. The included `example_timestamps.csv` illustrates the format; replace its example video name and times with your data.

Movement annotation is manual. Split intervals around pauses and handling; only the entered intervals count toward moving time. Applying edited timestamps preserves measurements for unchanged intervals and clears selections for changed boundaries.

## 3. Measure the endpoints

For each movement interval:

1. Click the same anatomical head landmark in the START and END frames, such as the nose or center of the head. Zoom for precise selection.
2. Check both cyan crosses. Orange dots are the perpendicular projections of the head points onto the rod.
3. Click **Confirm this measurement**, then continue to the next movement.

The calculation is:

```text
rod_position_cm = dot(head − A, B − A) / dot(B − A, B − A) × known_length_cm
distance_cm = abs(end_rod_position_cm − start_rod_position_cm)
moving_time_s = entered_end_time − entered_start_time
speed_cm_s = distance_cm / moving_time_s
```

This measures **start-to-end separation**, not cumulative head travel. If the animal advances 10 cm and then retreats 3 cm during one movement interval, its endpoint separation is 7 cm. If it returns to its starting position, the separation is zero. Perpendicular head movement does not add distance. Either orientation of A/B gives the same separation.

The recording's distance is the sum of the separate interval separations. Its moving time is the sum of their durations. Recording speed is total separation divided by total moving time, not the arithmetic mean of interval speeds. Repositioning between separate intervals is excluded.

These are endpoint-based speeds; reversals within one movement interval are canceled in its displacement. Intermediate frames are not tracked. If the selected head landmark is obscured at a boundary, its position cannot be measured reliably from that frame alone.

The actual timestamps of the nearest decoded frames are shown and exported alongside the entered times. Rounding to available frames can cause small differences; annotated movement time remains the entered end minus start. The current timestamp resolver requires frames within 0.06 seconds of the entered boundaries and is intended for ordinary laboratory video frame rates.

## Projects and export

**Save project** keeps the video reference, screenshot reference, known rod length, selected rod endpoints, entered timepoints, head selections, metadata, and confirmation states in one JSON file. **Open project** restores them. Paths are stored relative to the project file when possible. Keep the original video and screenshot available; the project does not embed media. The app can ask you to locate a missing video. If the screenshot is missing, existing video calibration and measurements can still be used.

**Export distance + speed** writes:

- `<recording_id>_endpoint_summary.csv`: recording distance, moving time, speed, and confirmation counts.
- `<recording_id>_endpoint_intervals.csv`: each movement's timestamps, head coordinates, rod positions, distance, speed, and review state.
- `<recording_id>_project.json`: the resumable integrated project.

Final distance and speed remain blank until the calibration and all required measurements are confirmed. Unconfirmed interval distances are available as `measured_separation_cm` for review. Genuine zero separation gives zero speed.

Exports use the recording identifier. To distinguish two videos with the same basename, give them different identifiers. Re-exporting the same video/identifier updates that recording's files. An existing project identifying a different video prevents accidental replacement under the same ID.

Changing physical rod length or calibration invalidates measurement confirmations; existing head coordinates are retained for review. Moving time is always derived from the manual interval annotations. Source video and screenshot files are read without modification.

## Summarize multiple recordings

Click **Summarize exports** and select a folder containing your exported CSVs, including any subfolders. The app creates `combined_endpoint_summary` beside the selected folder:

- `recording_summary.csv`
- `movement_intervals.csv`
- `animal_day_summary.csv`
- `animal_summary.csv`
- `validation_issues.csv`
- `summary_notes.txt`

Only validated, fully confirmed recordings enter group totals and averages. Identical duplicate exports count once; conflicting duplicates are flagged and excluded. Missing values remain blank. The script checks that interval separations and times sum to the recording values.

Group speed fields distinguish the arithmetic **mean recording speed** from **pooled speed** (combined distance divided by combined moving time). SD is sample standard deviation and is blank for fewer than two included recordings.

Animal and date fields are optional. Without an animal ID, the combined summary uses `Unspecified`. Animal-day groups require a supplied or recognized date; undated recordings still appear in the recording and animal summaries. Legacy identifiers such as `animal_r1_102126` are recognized, but are not required.

The summarizer is also available separately:

```sh
python summarize_exports.py "path/to/exports" --output "path/to/combined"
```

## GitHub files

Commit the source files in this folder, `requirements.txt`, this README, the Windows launcher, the example timestamp CSV, and `.gitignore`. Choose a license appropriate for your intended use and add a `LICENSE` file before offering reuse under that license.

The supplied `.gitignore` excludes environments, caches, recordings, project files, and measurement exports. No personal experimental data or machine-specific input paths are included in this package.
