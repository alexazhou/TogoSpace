import { createApp } from 'vue';
import App from './App.vue';
import router from './router';
import i18n, { syncLanguageFromBackend } from './i18n';
import '@fortawesome/fontawesome-free/css/fontawesome.min.css';
import '@fortawesome/fontawesome-free/css/solid.min.css';
import './theme/tokens.css';
import './style.css';

syncLanguageFromBackend().finally(() => {
  createApp(App).use(router).use(i18n).mount('#app');
});
