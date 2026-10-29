import { useEffect, useState } from 'react'
import { diffImages, diffText, ready } from './engine.js'

const TABS = { text: 'Text & data', image: 'Images', repo: 'Git repo' }
const TAB_KEY = 'bigdiff:tab'

function savedTab() {
  try {
    const t = localStorage.getItem(TAB_KEY)
    return TABS[t] ? t : 'text'
  } catch {
    return 'text'
  }
}

// One diff request at a time: result, error and busy for a tab.
function useRun(fn) {
  const [state, setState] = useState({})
  async function run(...args) {
    setState({ busy: true })
    try {
      setState({ out: await fn(...args) })
    } catch (e) {
      setState({ error: e.message })
    }
  }
  return [state, run]
}

export default function App() {
  const [tab, setTab] = useState(savedTab)
  const [loaded, setLoaded] = useState(false)
  const [bootError, setBootError] = useState(null)

  useEffect(() => { ready.then(() => setLoaded(true), e => setBootError(e.message)) }, [])

  function pick(t) {
    setTab(t)
    try { localStorage.setItem(TAB_KEY, t) } catch { /* private window */ }
  }

  return (
    <>
      <h1>big diff</h1>
      <p className="sub">Text, JSON and CSV values, or images. Runs in your browser; nothing is uploaded.</p>
      <nav role="tablist">
        {Object.entries(TABS).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => pick(k)}>{label}</button>
        ))}
      </nav>
      {bootError && <p className="status err">Couldn't load the diff engine: {bootError}</p>}
      {!loaded && !bootError && tab !== 'repo' && <p className="status">Loading the diff engine (first visit takes a few seconds)…</p>}
      {/* hidden, not unmounted, so pasted text and picked files survive a tab switch */}
      <div hidden={tab !== 'text'}><TextTab loaded={loaded} /></div>
      <div hidden={tab !== 'image'}><ImageTab loaded={loaded} /></div>
      <div hidden={tab !== 'repo'}><RepoTab /></div>
    </>
  )
}

function Status({ busy, error, msg }) {
  if (error) return <p className="status err">{error}</p>
  return busy ? <p className="status">…</p> : msg ? <p className="status">{msg}</p> : null
}

// A text box that also takes a dropped file; `side.name` is the dropped file's name (the engine
// picks JSON/CSV from it) until the text is edited.
function DropArea({ id, label, side, onChange }) {
  const [over, setOver] = useState(false)
  async function drop(e) {
    e.preventDefault()
    setOver(false)
    const f = e.dataTransfer.files[0]
    if (f) onChange({ text: await f.text(), name: f.name })
  }
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <textarea id={id} spellCheck={false} className={over ? 'over' : ''} value={side.text}
        onChange={e => onChange({ text: e.target.value })}
        onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)} onDrop={drop} />
    </div>
  )
}

function TextTab({ loaded }) {
  const [a, setA] = useState({ text: '' })
  const [b, setB] = useState({ text: '' })
  const [opts, setOpts] = useState({ kind: 'auto', key: '', tol: '0' })
  const [{ out, error, busy }, run] = useRun(diffText)
  const opt = k => e => setOpts({ ...opts, [k]: e.target.value })

  return (
    <section>
      <div className="pair">
        <DropArea id="ta" label="Before (paste, or drop a file)" side={a} onChange={setA} />
        <DropArea id="tb" label="After" side={b} onChange={setB} />
      </div>
      <div className="opts">
        <div><label htmlFor="kind">Compare as</label>
          <select id="kind" value={opts.kind} onChange={opt('kind')}>
            <option value="auto">auto</option><option value="text">lines</option>
            <option value="json">JSON values</option><option value="csv">CSV rows</option>
          </select></div>
        <div><label htmlFor="key">Match rows by field</label><input id="key" placeholder="e.g. id" value={opts.key} onChange={opt('key')} /></div>
        <div><label htmlFor="tol">Number tolerance</label><input id="tol" type="number" step="any" value={opts.tol} onChange={opt('tol')} /></div>
        <button className="go" disabled={!loaded || busy} onClick={() => run({ a: a.text, b: b.text, ...opts, names: [a.name, b.name] })}>Diff</button>
      </div>
      <Status busy={busy} error={error} msg={out?.summary} />
      {out && <TextResult r={out} />}
    </section>
  )
}

function TextResult({ r }) {
  if (r.kind !== 'text') {
    return r.ops.length > 0 && <div className="wrap"><ul className="ops">{r.ops.map((o, i) => <li key={i} className={o.op}>{o.text}</li>)}</ul></div>
  }
  return r.rows.length > 0 && (
    <div className="wrap"><table className="sbs"><tbody>
      {r.rows.map((x, i) => x.t === 'gap'
        ? <tr key={i} className="gap"><td colSpan={4}>{x.n} unchanged line(s)</td></tr>
        : <tr key={i} className={x.t}><td className="n">{x.an}</td><td className="a">{x.a}</td><td className="n">{x.bn}</td><td className="b">{x.b}</td></tr>)}
    </tbody></table></div>
  )
}

async function imageDiff(files, threshold) {
  const [a, b] = await Promise.all(files.map(async f => new Uint8Array(await f.arrayBuffer())))
  return { ...await diffImages({ a, b, threshold }), a: URL.createObjectURL(files[0]), b: URL.createObjectURL(files[1]) }
}

function ImageTab({ loaded }) {
  const [files, setFiles] = useState({})
  const [threshold, setThreshold] = useState('16')
  const [{ out, error, busy }, run] = useRun(imageDiff)

  useEffect(() => () => { if (out) [out.a, out.b].forEach(URL.revokeObjectURL) }, [out])

  const pick = k => e => setFiles({ ...files, [k]: e.target.files[0] })
  return (
    <section>
      <div className="pair">
        <div><label htmlFor="ia">Before image</label><input id="ia" type="file" accept="image/*" onChange={pick('a')} /></div>
        <div><label htmlFor="ib">After image</label><input id="ib" type="file" accept="image/*" onChange={pick('b')} /></div>
      </div>
      <div className="opts">
        <div><label htmlFor="thr">Threshold (0–255)</label><input id="thr" type="number" min="0" max="255" value={threshold} onChange={e => setThreshold(e.target.value)} /></div>
        <button className="go" disabled={!loaded || busy || !files.a || !files.b} onClick={() => run([files.a, files.b], threshold)}>Diff</button>
      </div>
      <Status busy={busy} error={error} msg={out?.summary} />
      {out && (
        <div className="imgs">
          <figure><img src={out.a} alt="before" /><figcaption>Before</figcaption></figure>
          <figure><img src={out.b} alt="after" /><figcaption>After</figcaption></figure>
          <figure><img src={out.heatmap} alt="changed pixels in red" /><figcaption>Changed pixels (red)</figcaption></figure>
        </div>
      )}
    </section>
  )
}

function RepoTab() {
  return (
    <section>
      <p>Code review needs your repo, so it runs on your machine with the bigdiff command line tool. From any repo:</p>
      <pre className="cmd">uv run --project ~/big-diff bigdiff review main      # changed symbols, riskiest first, with their uses
uv run --project ~/big-diff bigdiff serve            # this page, plus a Git repo tab</pre>
    </section>
  )
}
