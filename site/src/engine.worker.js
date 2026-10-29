// Runs big diff's own Python (data.py, image.py, diffs.py) under Pyodide, so the hosted page and
// `bigdiff serve` share one engine and files never leave the browser.
import init from '../../bigdiff/__init__.py?raw'
import data from '../../bigdiff/data.py?raw'
import image from '../../bigdiff/image.py?raw'
import diffs from '../../bigdiff/diffs.py?raw'

const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v314.0.7/full/'

async function boot() {
  const { loadPyodide } = await import(/* @vite-ignore */ PYODIDE + 'pyodide.mjs')
  const py = await loadPyodide({ indexURL: PYODIDE, packages: ['pillow'] })
  py.FS.mkdirTree('/app/bigdiff')
  for (const [name, src] of Object.entries({ __init__: init, data, image, diffs })) py.FS.writeFile(`/app/bigdiff/${name}.py`, src)
  py.runPython('import sys; sys.path.insert(0, "/app")')
  const handle = py.pyimport('bigdiff.diffs').handle
  // Uint8Arrays become memoryviews, which diffs.handle reads as bytes.
  return (op, req) => handle(op, py.toPy(req)).toJs({ dict_converter: Object.fromEntries })
}

const ready = boot()

// Python's last traceback line is the useful part ("ValueError: ...").
const message = e => String(e?.message ?? e).trim().split('\n').pop()

self.onmessage = async ({ data: { id, op, req } }) => {
  try {
    const handle = await ready
    self.postMessage({ id, result: op === 'ping' || handle(op, req) })
  } catch (e) {
    self.postMessage({ id, error: message(e) })
  }
}
