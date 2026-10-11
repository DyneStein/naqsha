// Plain-language text for everything the backend reports in technical terms.

const STAGE: [RegExp, string][] = [
  [/scanning/i, 'Checking video quality (blur, exposure)'],
  [/selecting frames/i, 'Choosing the best photos from the video'],
  [/exporting frames/i, 'Extracting photos from the video'],
  [/checking image links/i, 'Checking that neighbouring photos overlap properly'],
  [/densifying/i, 'Adding extra photos where overlap was weak'],
  [/geotagging/i, 'Writing GPS position into each photo'],
  [/reading telemetry/i, 'Reading GPS from the videos'],
  [/cleaning GPS/i, 'Cleaning GPS jumps'],
  [/terrain/i, 'Getting ground height (terrain model)'],
  [/coverage/i, 'Working out what ground the camera saw'],
  [/checking ODM|orientation/i, 'Preparing the stitching engine'],
  [/detect_features/i, 'Finding recognisable points in each photo'],
  [/match_features/i, 'Matching photos to each other'],
  [/create_tracks/i, 'Linking matched points across photos'],
  [/reconstruct|bundle/i, 'Working out exact camera positions in 3D'],
  [/undistort|export_/i, 'Correcting lens distortion'],
  [/openmvs/i, 'Building the dense 3D surface'],
  [/filterpoints/i, 'Cleaning the 3D points'],
  [/meshing/i, 'Building the ground surface'],
  [/texturing/i, 'Painting the photos onto the surface'],
  [/georeferencing/i, 'Placing the result on the map'],
  [/odm_dem/i, 'Making the elevation map'],
  [/odm_orthophoto/i, 'Making the top-down map'],
  [/report|postprocess/i, 'Finishing up'],
  [/joining parts/i, 'Joining the map parts together'],
  [/quality check/i, 'Checking the quality of the result'],
  [/dataset|starting/i, 'Loading the photos'],
]

export function friendlyStage(stage?: string | null): string {
  if (!stage) return 'Waiting'
  const part = stage.match(/^part (\d+) of (\d+):\s*(.*)$/i)
  const core = part ? part[3] : stage
  const hit = STAGE.find(([re]) => re.test(core))
  const text = hit ? hit[1] : core
  return part ? `Part ${part[1]} of ${part[2]} · ${text}` : text
}

export const JOB_TITLE: Record<string, string> = {
  analyze: 'Analysing the flights',
  select: 'Choosing photos from the videos',
  reconstruct: 'Stitching the map',
  pipeline: 'Building the map',
  pull_odm: 'Downloading the stitching engine',
  trees: 'Counting trees',
  trees_train: 'Teaching Naqsha your trees',
}

export function cameraUse(pitch?: number | null): { label: string; detail: string; kind: string } {
  if (pitch == null) return { label: 'unknown', detail: '', kind: 'cancelled' }
  if (pitch <= -60) return { label: 'straight down', detail: 'best for the map', kind: 'good' }
  if (pitch <= -30) return { label: 'angled', detail: 'usable, with care', kind: 'fair' }
  return { label: 'at horizon', detail: 'not usable for a map', kind: 'cancelled' }
}

export function qualitySentence(qa: any): string {
  if (!qa) return ''
  const reg = qa.registration ?? {}
  const sfm = qa.sfm ?? {}
  const parts: string[] = []
  if (reg.images_in) {
    parts.push(reg.registered === reg.images_in
      ? `All ${reg.images_in} photos fitted together`
      : `${reg.registered} of ${reg.images_in} photos fitted together`)
  }
  if ((reg.pieces ?? 1) > 1) parts.push(`the map is in ${reg.pieces} separate pieces`)
  if (sfm.gps_error_m != null) parts.push(`positions agree with GPS to about ${Number(sfm.gps_error_m).toFixed(1)} m`)
  if (qa.flagged_images?.length) parts.push(`${qa.flagged_images.length} photos looked misplaced`)
  if (qa.holes) parts.push(`${Math.round((1 - qa.holes.hole_fraction) * 100)}% of the flown area is filled in`)
  return parts.join(', ') + '.'
}

export const GRADE_TEXT: Record<string, string> = {
  good: 'Good: ready to use',
  fair: 'Usable, with some gaps or weak spots',
  poor: 'Poor: large parts missing or misplaced',
  failed: 'Failed',
}
