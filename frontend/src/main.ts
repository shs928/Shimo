import { createApp } from 'vue'
import App from './App.vue'
import { initTheme } from './theme'
import 'katex/dist/katex.min.css'
import './style.css'

initTheme()
createApp(App).mount('#app')
