import { createRoot } from 'react-dom/client'
import '../../bigdiff/tokens.css' // the packet's colours
import '../../bigdiff/web.css' // the same styles as `bigdiff serve`
import App from './App.jsx'

createRoot(document.getElementById('root')).render(<App />)
