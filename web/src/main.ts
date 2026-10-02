import { mount } from 'svelte';
import App from './App.svelte';
import './styles/tokens.css';
import './app.css';

const target = document.getElementById('app');
if (!target) throw new Error('Missing #app');

export default mount(App, { target });
