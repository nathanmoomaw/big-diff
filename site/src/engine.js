// Promise wrapper around the Pyodide worker. The worker starts loading on first import.
const worker = new Worker(new URL('./engine.worker.js', import.meta.url), { type: 'module' })
const pending = new Map()
let next = 0

worker.onmessage = ({ data: { id, result, error } }) => {
  const p = pending.get(id)
  pending.delete(id)
  if (error) p.reject(new Error(error))
  else p.resolve(result)
}

// `req` is what `bigdiff serve` gets as JSON: diffs.handle coerces it for both.
function call(op, req = {}, transfer = []) {
  const id = next++
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    worker.postMessage({ id, op, req }, transfer)
  })
}

export const ready = call('ping')
export const diffText = req => call('text', req)
export const diffImages = req => call('image', req, [req.a.buffer, req.b.buffer])
