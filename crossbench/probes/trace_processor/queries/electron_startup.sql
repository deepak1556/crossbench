-- Summarize actionable Electron/Chromium startup activity from core Perfetto
-- tables. Durations are inclusive slice durations and can overlap.
WITH analysis_bounds AS (
  SELECT
    coalesce(
      (SELECT min(ts)
       FROM slice
       WHERE name = 'vscode.window-resize.measure.start'),
      start_ts
    ) AS start_ts,
    coalesce(
      (SELECT max(ts)
       FROM slice
       WHERE name = 'vscode.window-resize.measure.end'),
      end_ts
    ) AS end_ts
  FROM trace_bounds
)
SELECT
  'v8' AS area,
  CASE
    WHEN lower(coalesce(category, '')) LIKE '%compile%' THEN 'compile'
    WHEN lower(coalesce(category, '')) LIKE '%gc%' THEN 'gc'
    WHEN lower(coalesce(category, '')) LIKE '%execute%' THEN 'execute'
    WHEN lower(name) LIKE '%cpuprofiler%' THEN 'cpu_profiler'
    ELSE 'other'
  END AS metric,
  count(*) AS slice_count,
  sum(CASE WHEN dur > 0 THEN dur ELSE 0 END) / 1000000.0 AS duration_ms
FROM slice s
JOIN analysis_bounds b ON s.ts BETWEEN b.start_ts AND b.end_ts
WHERE lower(coalesce(s.category, '')) LIKE '%v8%'
   OR lower(s.name) LIKE '%cpuprofiler%'
GROUP BY area, metric

UNION ALL

SELECT
  'main_thread' AS area,
  CASE t.name
    WHEN 'CrBrowserMain' THEN 'browser'
    WHEN 'CrRendererMain' THEN 'renderer'
    WHEN 'CrGpuMain' THEN 'gpu'
    WHEN 'node.CrUtilityMain' THEN 'node'
    ELSE t.name
  END AS metric,
  count(*) AS slice_count,
  sum(CASE WHEN s.dur > 0 THEN s.dur ELSE 0 END) / 1000000.0 AS duration_ms
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN analysis_bounds b ON s.ts BETWEEN b.start_ts AND b.end_ts
WHERE t.name IN (
  'CrBrowserMain',
  'CrRendererMain',
  'CrGpuMain',
  'node.CrUtilityMain'
)
GROUP BY area, metric

UNION ALL

SELECT
  'frame_scheduler' AS area,
  name AS metric,
  count(*) AS slice_count,
  sum(CASE WHEN dur > 0 THEN dur ELSE 0 END) / 1000000.0 AS duration_ms
FROM slice s
JOIN analysis_bounds b ON s.ts BETWEEN b.start_ts AND b.end_ts
WHERE s.name = 'PipelineReporter'
   OR s.name = 'ChromeFrameReporter2'
   OR s.name LIKE 'DisplayScheduler::%'
   OR s.name LIKE 'Scheduler::%'
GROUP BY area, metric

UNION ALL

SELECT
  'frame_state' AS area,
  a.display_value AS metric,
  count(*) AS slice_count,
  sum(CASE WHEN s.dur > 0 THEN s.dur ELSE 0 END) /
      1000000.0 AS duration_ms
FROM slice s
JOIN args a USING (arg_set_id)
JOIN analysis_bounds b ON s.ts BETWEEN b.start_ts AND b.end_ts
WHERE s.name IN ('PipelineReporter', 'ChromeFrameReporter2')
  AND a.key = 'frame_reporter.state'
GROUP BY area, metric

UNION ALL

SELECT
  'app_mark' AS area,
  name AS metric,
  count(*) AS slice_count,
  sum(CASE WHEN dur > 0 THEN dur ELSE 0 END) / 1000000.0 AS duration_ms
FROM slice s
JOIN analysis_bounds b ON s.ts BETWEEN b.start_ts AND b.end_ts
WHERE s.name LIKE 'vscode.%'
GROUP BY area, metric

ORDER BY area, duration_ms DESC, metric;
