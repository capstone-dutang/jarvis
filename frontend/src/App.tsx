import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Home from './pages/Home'
import Workflows from './pages/Workflows'
import Search from './pages/Search'

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/search" element={<Search />} />
      </Route>
    </Routes>
  )
}

export default App
