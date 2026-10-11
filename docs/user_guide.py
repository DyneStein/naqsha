import sys
from reportlab.platypus import PageBreak, Preformatted, SimpleDocTemplate

from pdfstyle import *  # noqa: F401,F403

SH = sys.argv[1]
OUT = sys.argv[2]
st = styles(9.8)
P = lambda t, s="body": Paragraph(t, st[s])  # noqa: E731
story = []


def shot(name, caption, width=WIDTH, max_h=None, head=None):
    story.append(KeepTogether(([P(head, "h1")] if head else []) + [image(SH + name, width, max_h), P(caption, "caption")]))


def side(name, text_flow, img_w=78 * mm, max_h=None, caption=""):
    """Screenshot on the right, explanation on the left."""
    img = image(SH + name, img_w, max_h)
    left = [x if not isinstance(x, str) else P(x) for x in text_flow]
    t = Table([[left, [img, P(caption, "caption")] if caption else img]],
              colWidths=[WIDTH - img_w - 6 * mm, img_w + 6 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                           ("RIGHTPADDING", (0, 0), (0, -1), 8)]))
    story.append(t)


# ------------------------------------------------------------------ cover
story.append(Spacer(1, 30 * mm))
story.append(P("Naqsha User Guide", "title"))
story.append(P("How to turn drone videos into a map: every screen, every button, and where your files go.", "subtitle"))
story.append(Spacer(1, 4 * mm))
shot("03_project.png", "The main screen after a map has been built: the flight summary at the top, the map in the middle "
     "with its key on the left, and the Build / Maps / Videos / Activity panel on the right. Every small (i) explains the "
     "thing next to it when you hover over it.")
story.append(P("<b>In one sentence:</b> point Naqsha at a folder of drone videos, press <b>Build the map</b>, wait, "
               "and download a stitched top-down map with coordinates, an elevation map and the flight paths. "
               "Everything in between is automatic."))
story.append(P("<b>Contents.</b> 1 Starting the app. 2 Creating a project. 3 Reading the flight summary. 4 The map and its "
               "key. 5 Looking at individual photos. 6 Building a map. 7 Watching progress. 8 The Maps tab: quality and "
               "downloads. 9 Exploring the result. 10 The Videos tab. 11 Counting trees. 12 Where your files are, and changing a map. "
               "13 Running without the browser. 14 Troubleshooting. <b>Tip:</b> anywhere in the app, hover over a small (i) "
               "for a plain explanation of what that part does."))
story.append(PageBreak())

# ------------------------------------------------------------------ 1
story.append(P("1. Starting the app", "h1"))
story.append(P("Naqsha runs on your own computer. Nothing is uploaded anywhere."))
story += bullets([
    "<b>Start Docker Desktop</b> first (the whale icon in the menu bar). The stitching engine runs inside it.",
    "Open <b>Terminal</b>, go to the <font name='Mono'>naqsha</font> folder and run <font name='Mono'>./run.sh</font>. "
    "Leave this window open while you work.",
    "Open <b>http://127.0.0.1:8765</b> in your browser (Safari or Chrome).",
], st)
story.append(Preformatted('cd ~/Desktop/Macbook/Work/Internship/DenseFusion/Projects/Stiching/naqsha\n./run.sh', st["code"]))
story.append(P("The small labels at the top right show whether the computer is ready: <b>Docker</b>, <b>Stitching engine</b> "
               "(installed), <b>CPU only</b> or <b>GPU</b>, how much memory the stitching engine may use, and free disk "
               "space; the (i) at the end explains each. A label turns saffron when something deserves attention, for example less "
               "than 20 GB of free disk. If an NVIDIA graphics card is available (on the GPU computer), Naqsha uses it "
               "automatically; on a Mac, stitching uses the processor."))
story.append(P("The sun and moon button at the far top right switches between dark and light mode; the app remembers your choice."))
shot("01_welcome.png", "The start screen. Your projects are listed on the left; the button at the top left hides or shows that list.", max_h=95 * mm)

# ------------------------------------------------------------------ 2
story.append(P("2. Creating a project", "h1"))
side("02_new_project.png", [
    "Click <b>New project</b>. Give it a name, then pick the folder that holds the drone's video files: click folder "
    "names to go into them and <b>up</b> to go back. The count at the top right tells you how many videos are in the "
    "folder you are looking at.",
    "Click <b>Create and analyse</b>. The videos are read where they are; nothing is copied.",
    "Within about a minute Naqsha reads the GPS, height and camera angle stored inside every video and draws the "
    "flights on the map. This is step 1 of the three steps shown next to the project name; the current step is "
    "highlighted in saffron and finished steps get a tick.",
], img_w=86 * mm, caption="Choosing the video folder.")

# ------------------------------------------------------------------ 3
story.append(P("3. Reading the flight summary", "h1"))
story.append(P("The strip of numbers under the project name answers \"where did the drone fly?\". Hover over the (i) next "
               "to any number for a short explanation."))
story.append(table([
    ["Number", "What it means"],
    ["Flight time", "total length of all videos"],
    ["Path flown", "distance the drone travelled, from its cleaned GPS track"],
    ["Max from takeoff", "the farthest the drone got from where it took off"],
    ["Covering radius", "radius of the smallest circle that contains the whole flight (its centre is in the tooltip)"],
    ["Ground seen", "all the ground any camera frame showed"],
    ["Mappable", "ground seen with the camera at least 30 degrees below the horizon (usable for a map)"],
    ["Straight-down", "ground seen with the camera pointing almost straight down (best quality)"],
    ["Max height", "highest point above the ground, using a terrain model"],
], [38 * mm, WIDTH - 38 * mm], st))
story.append(P("At the right end of the strip, the (i) beside \"4 flights\" shows each flight's takeoff point and any notes, "
               "for example that the drone's own altitude readings disagreed between batteries and were corrected."))

# ------------------------------------------------------------------ 4
story.append(P("4. The map and its key", "h1"))
side("04_map_key.png", [
    "The <b>Map key</b> panel on the left of the map names every colour and line and lets you switch each one on or "
    "off. The (i) on each row explains it.",
    "<b>Background</b>: a street map, or old satellite imagery for comparison (several years old).",
    "<b>Your stitched map</b>: the finished map, with a slider to fade it in and out; the <b>elevation</b> colours; and "
    "<b>gaps</b> (red) where the drone flew but nothing could be stitched.",
    "<b>Flight paths</b>: one colour per video, with whether its camera angle is useful for mapping. Untick a clip to "
    "hide its line.",
    "<b>Planning layers</b>: the ground the camera saw (green), and the photos used for the map (white dots).",
    "The cross closes the key; the <b>Map key</b> button brings it back.",
], img_w=62 * mm)
side("05_hover_c.png", [
    "<b>Hover to find out what anything is.</b> Moving the mouse over a flight path, takeoff point, coverage area or gap "
    "shows a short explanation.",
    "Zoom with the mouse wheel or the + and - buttons; drag to move around.",
], img_w=96 * mm, caption="Hovering over a flight path says which video and flight it is.")

# ------------------------------------------------------------------ 5
story.append(P("5. Looking at individual photos", "h1"))
side("06_photo_c.png", [
    "Each white dot is one video frame that was picked as a photo for the map. Click a dot to see the photo, when it "
    "was taken in the video, its height above the ground, camera angle, heading and position accuracy.",
    "Click the photo to open it full size.",
    "After a map is built, dots turn <b>red</b> if the photo landed far from its GPS position (it is left out "
    "automatically) and <b>purple</b> if it could not be fitted in.",
], img_w=86 * mm, caption="A photo picked from the video, with where and how it was taken.")

# ------------------------------------------------------------------ 6
story.append(P("6. Building a map", "h1"))
side("07_build.png", [
    "Open the <b>Build</b> tab (step 2).",
    "<b>Pick the clips.</b> Naqsha ticks the useful ones for you. Clips marked <i>at horizon</i> filmed the horizon "
    "and add little; <i>angled</i> clips are usable; <i>straight down</i> is best.",
    "<b>Pick a quality.</b> <i>Quick preview</i> takes minutes and is good for checking coverage. <i>Standard</i> "
    "(recommended) gives about 2.5 cm per pixel plus an elevation map. <i>Maximum detail</i> is meant for the GPU computer.",
    "Press <b>Build the map</b>.",
    "<b>Advanced settings</b> (optional, folded away until you open it): use angled frames; overlap between neighbouring photos (85% is right for "
    "trees, more is safer but slower); the lowest height to use; a cap on the number of photos; and auto-repair, which "
    "rebuilds once if the check finds misplaced photos. <i>Only pick the photos</i> prepares a photo set without "
    "stitching, so you can inspect it first.",
    "Large surveys are processed in parts that fit in this computer's memory and joined at the end. You do not need to "
    "do anything about this.",
], img_w=82 * mm)

# ------------------------------------------------------------------ 7
shot("14_progress.png", "A running job: what it is doing in plain words, the percentage and an estimate of the time left.",
     max_h=105 * mm, head="7. Watching progress")
story += bullets([
    "A progress card at the top of the right-hand panel shows the current stage, for example \"Matching photos to each other\" or "
    "\"Part 4 of 17: Building the dense 3D surface\", with an estimated time left.",
    "<b>Stop</b> cancels the job. A stopped or failed map keeps the parts that already finished, and <b>Retry</b> on the "
    "map card continues from there.",
    "You can close the browser; the work continues as long as the Terminal window running <font name='Mono'>./run.sh</font> stays open.",
    "Timing on an 8 GB Mac: reading the videos about a minute; a quick preview of 300 photos about 10 minutes; a "
    "standard map of 850 photos several hours. The GPU computer is much faster.",
    "If the computer runs short of memory, the affected part is split and redone automatically. You will see the plan "
    "grow (for example from 15 to 21 parts); this is normal.",
], st)

# ------------------------------------------------------------------ 8
story.append(P("8. The Maps tab: quality and downloads", "h1"))
side("08_maps.png", [
    "Every map gets a verdict in plain words (<b>Good: ready to use</b>, <b>Usable, with some gaps</b>, or <b>Poor</b>) "
    "followed by one sentence that sums it up, then the numbers below. Each number has an (i) explaining it.",
    "<b>On map</b> / <b>Show on map</b> displays that map; <b>Log</b> shows the engine's technical log; <b>Delete</b> "
    "removes the map (the videos are never touched).",
    "<b>Downloads</b> at the bottom of the card: the map image, the elevation map, a 3D point cloud and a technical "
    "report. The (i) on each says what the file is and which programs open it.",
], img_w=72 * mm, caption="A finished map: verdict, numbers and downloads.")
story.append(table([
    ["Item", "What it tells you"],
    ["Photos fitted", "how many photos the engine could place; ideally all"],
    ["Position vs GPS", "typical difference between where the map puts each photo and the drone's GPS"],
    ["Detail", "the size of one map pixel on the ground"],
    ["Area mapped", "how much ground the map covers, and what share of the flown area that is"],
    ["Map pieces", "1 means everything joined into one consistent map"],
    ["Misplaced photos", "photos that landed far from their GPS (left out by auto-repair)"],
    ["Parts aligned", "for big maps: how far apart the parts were before and after joining"],
], [38 * mm, WIDTH - 38 * mm], st))
story.append(P("Past jobs (finished, stopped or failed) are listed in the <b>Activity</b> tab."))

# ------------------------------------------------------------------ 9
story.append(P("9. Exploring the result", "h1"))
half = (WIDTH - 6 * mm) / 2
t = Table([[image(SH + "11_detail.png", half, 92 * mm), image(SH + "12_elevation.png", half, 92 * mm)]],
          colWidths=[half + 3 * mm, half + 3 * mm])
t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
story.append(t)
story.append(P("Left: the stitched map close up (2.5 cm per pixel). Right: the elevation colours, where trees stand out "
               "from the ground.", "caption"))
t = Table([[image(SH + "13_satellite.png", half, 92 * mm), image(SH + "15_gaps.png", half, 92 * mm)]],
          colWidths=[half + 3 * mm, half + 3 * mm])
t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
story.append(t)
story.append(P("Left: the new map over the old satellite background, with red gaps at its edges. Right: flight paths, "
               "coverage (green) and gaps (red) over the street map.", "caption"))
side("10_measure_c.png", [
    "<b>Measuring.</b> Click <b>Measure</b> (top left of the map), then click points on the map. The box shows the total "
    "distance, and the enclosed area once you have three or more points. Click <b>Stop measuring</b> to finish.",
    "Use the elevation layer and the fade slider together to compare the map with the background.",
], img_w=92 * mm, caption="Measuring a boundary: distance and enclosed area.")

# ------------------------------------------------------------------ 10
story.append(P("10. The Videos tab", "h1"))
side("09_videos.png", [
    "One row per video: its length, typical height above the ground and how much of it looked straight down. Click a "
    "row for the rest: distance flown, which flight it belonged to, camera angle and GPS quality.",
    "GPS jumps (sudden steps in the drone's position) are shown in saffron. They are smoothed automatically.",
    "The badge on each row says whether the clip is useful for a map.",
], img_w=72 * mm)

# ------------------------------------------------------------------ 11
story.append(P("11. Counting trees", "h1"))
side("16_trees_panel.png", [
    "Open the <b>Trees</b> tab on a finished map and press <b>Count trees</b> (about two minutes; five with a model "
    "taught on your site). Naqsha finds every tree crown in the map image with <b>DeepForest</b>, a deep learning "
    "model trained on hand-labelled crowns, and measures each tree's height from the elevation map.",
    "<b>Taller than</b> picks what counts as a tree: <i>All</i> includes everything woody from 1 m (young trees and "
    "bushes); 4 m counts established trees only. The headline, the map dots and the downloads all follow it, and it is "
    "saved for the map. <b>By height</b> shows how many fall in each band, so you can read any definition.",
    "<b>Not yet checked</b> means the number has not been compared with a hand count. Mark every tree in a few random "
    "<b>test squares</b> and Naqsha measures its own accuracy and gives a range.",
], img_w=70 * mm, caption="The Trees tab: count, height filter and height bands.")
side("17_trees_map.png", [
    "Each dot is one tree, coloured by height (teal short, saffron tall). Hover a dot for its height, crown width and "
    "how it was found. In the map key, <b>Crown outlines</b> and <b>Tree density</b> (trees per hectare in 20 m "
    "squares) can be switched on.",
], img_w=86 * mm, caption="Trees on the map, at least 4 m tall.")
side("18_marking.png", [
    "<b>Teaching the model your trees.</b> The image model was trained elsewhere, so it improves a lot when it sees this "
    "site. In <b>Active learning</b> press <b>Ask me</b>: Naqsha places small 12 m patches where it is least sure and "
    "takes you to each in turn. Click every tree inside, then <b>Next</b>; a patch with no trees is a real answer.",
    "Your own trees are hidden while you mark, so they cannot steer you. Press <b>Learn from my answers and count "
    "again</b> (about five minutes); repeat with <b>Ask a new round</b> for further improvement.",
], img_w=96 * mm, caption="Marking a patch: click every tree inside the square.")
side("19_trees_active.png", [
    "<b>Test squares</b> (saffron) are placed at random and only grade the count. <b>Teaching squares</b> and "
    "active-learning patches (teal) only teach. Keeping them apart is what makes the accuracy honest.",
    "Teaching always learns from <b>everything</b> marked on this site: random teaching squares and active-learning "
    "answers together, so both approaches improve one model.",
    "<b>Starting point</b>: every new project starts from the plain DeepForest model, so another site never steers "
    "it. If a site with similar vegetation was already taught, you can start from that model instead. <b>Model</b> "
    "lists this site's taught versions; switch and press <b>Count again</b>.",
    "Mark young trees and bushes the same way everywhere: whatever you mark is what the model learns to count. The "
    "height filter then decides what you report.",
], img_w=62 * mm, caption="Teaching, active learning and model versions.")

# ------------------------------------------------------------------ 12
story.append(P("12. Where your files are, and changing a map", "h1"))
story.append(P("<b>The finished Shakarparian map</b> is in the <b>Shakarparian Map (Naqsha output)</b> shortcut on your "
               "Desktop, which opens this folder:"))
story.append(Preformatted("Desktop/Macbook/Work/Internship/DenseFusion/Projects/Stiching/\n"
                          "    1 October 2026 Shakarparia scan/naqsha-output/", st["code"]))
story.append(table([
    ["File", "What it is", "Open it with"],
    ["map.tif", "the stitched map with coordinates (427 MB)", "QGIS (free), ArcGIS, Google Earth Pro, Global Mapper"],
    ["map_preview.jpg", "a quick-look picture of the whole map", "any image viewer"],
    ["elevation.tif", "surface height for every pixel", "QGIS, ArcGIS"],
    ["flight.kml", "flight paths and takeoff points", "Google Earth"],
    ["coverage.geojson, track.geojson", "covered ground and tracks", "QGIS, geojson.io"],
    ["quality.json", "the full quality report", "any text editor"],
], [42 * mm, 62 * mm, WIDTH - 104 * mm], st))
story.append(P("Everything else Naqsha keeps (projects, extracted photos, parts of maps) lives in "
               "<font name='Mono'>Stiching/naqsha-data/</font>. Delete projects from the app rather than by hand."))
story.append(P("<b>Can I change the map?</b> Yes, by building it again with different choices: other clips, another "
               "quality, more overlap, or angled frames on or off. Each build becomes a new entry in the <b>Maps</b> tab, "
               "so you can compare them and delete the ones you do not need. A photo set can also be re-stitched at a "
               "different quality with <b>Make a map from these photos</b>, without picking photos again. Naqsha does not "
               "edit map pixels by hand; for drawing, cutting or annotating, open <font name='Mono'>map.tif</font> in QGIS. "
               "For better absolute accuracy (survey grade), a few ground control points measured on site are needed; that "
               "is a planned addition."))

# ------------------------------------------------------------------ 13
story.append(P("13. Running without the browser", "h1"))
story.append(P("One command does everything, from reading the videos to exporting the files, without any clicking:"))
story.append(Preformatted('./run.sh process "/path/to/video folder"\n'
                          './run.sh process "/path/to/video folder" --quality preview --out ~/Desktop/my-map\n'
                          './run.sh process "/path/to/video folder" --trees --tree-min-height 4',
                          st["code"]))
story.append(P("It prints progress as it goes and writes the results to <font name='Mono'>naqsha-output</font> next to the "
               "videos (or to the folder given with <font name='Mono'>--out</font>). The project also appears in the web "
               "app, so you can look at it there afterwards."))

# ------------------------------------------------------------------ 14
story.append(P("14. Troubleshooting", "h1"))
story.append(table([
    ["What you see", "What to do"],
    ["<b>Docker off</b> label, or \"Docker is not running\"", "start Docker Desktop and wait until its whale icon is steady"],
    ["<b>Download stitching engine</b> label", "click it once; the stitching engine (about 2 GB) downloads in the background"],
    ["A map failed", "open the <b>Maps</b> tab, read the first line of the error, and press <b>Retry</b>; finished parts are kept"],
    ["\"ran out of memory even after splitting\"", "close other apps, or choose a lower quality; on the Mac keep Docker at 5 GB"],
    ["The Mac feels slow during a build", "normal: stitching uses all processor cores. Light use (browsing, video) is fine"],
    ["Disk label turns saffron", "free space, or delete old maps and photo sets in the app"],
    ["A clip says \"no telemetry\"", "that video has no GPS stored in it and cannot be placed on the map"],
    ["Big red gaps over trees", "dense canopy is hard to stitch; fly higher or with more overlap next time"],
], [62 * mm, WIDTH - 62 * mm], st))

doc = SimpleDocTemplate(OUT, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=14 * mm,
                        bottomMargin=16 * mm, title="Naqsha User Guide", author="DenseFusion",
                        subject="How to use Naqsha")
doc.build(story, onFirstPage=footer("Naqsha user guide"), onLaterPages=footer("Naqsha user guide"))
