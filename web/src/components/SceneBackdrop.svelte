<script lang="ts">
  // Full-viewport canvas behind the UI (three.js, lazily loaded) with an
  // animated CSS gradient underneath as fallback when WebGL is unavailable,
  // still loading or turned off.
  import { app } from '../lib/app.svelte';
  import { prefs } from '../lib/prefs.svelte';
  import { deriveSceneState } from '../lib/scene-state';
  import { sceneHost } from '../lib/scene-host.svelte';

  sceneHost.configure({ quality: prefs.effects, reducedMotion: prefs.reducedMotion });

  $effect(() => {
    sceneHost.setQuality(prefs.effects);
  });

  $effect(() => {
    sceneHost.apply(deriveSceneState(app.focusTurn));
  });

  const mood = $derived(deriveSceneState(app.focusTurn).mood);
</script>

<div class="backdrop" data-scene={sceneHost.status} data-mood={mood} aria-hidden="true">
  <div class="fallback">
    <span class="blob claude"></span>
    <span class="blob chatgpt"></span>
    <span class="blob core"></span>
  </div>
  <canvas class="scene" {@attach sceneHost.attach}></canvas>
  <div class="vignette"></div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 0;
    overflow: hidden;
    background: radial-gradient(ellipse at 50% 120%, #10132a 0%, var(--bg) 60%);
    pointer-events: none;
  }

  .scene {
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
    opacity: 0;
    transition: opacity 1.2s var(--ease-out);
  }

  [data-scene='on'] .scene {
    opacity: 1;
  }

  .fallback {
    position: absolute;
    inset: 0;
    transition: opacity 1.2s var(--ease-out);
  }

  [data-scene='on'] .fallback {
    opacity: 0;
  }

  .blob {
    position: absolute;
    width: 46vmax;
    height: 46vmax;
    border-radius: 50%;
    filter: blur(70px);
    opacity: 0.32;
    will-change: transform;
  }

  .blob.claude {
    top: -12vmax;
    left: -10vmax;
    background: radial-gradient(circle, var(--claude) 0%, transparent 68%);
    animation: drift-a 26s ease-in-out infinite alternate;
  }

  .blob.chatgpt {
    right: -12vmax;
    bottom: -14vmax;
    background: radial-gradient(circle, var(--chatgpt) 0%, transparent 68%);
    animation: drift-b 30s ease-in-out infinite alternate;
  }

  .blob.core {
    top: 30%;
    left: 35%;
    width: 30vmax;
    height: 30vmax;
    background: radial-gradient(circle, #6d5bd0 0%, transparent 70%);
    opacity: 0.18;
    animation: drift-c 22s ease-in-out infinite alternate;
  }

  /* The fallback reacts to the turn too (no WebGL needed). */
  [data-mood='thinking'] .blob,
  [data-mood='speaking'] .blob {
    opacity: 0.45;
  }

  [data-mood='debate'] .blob.core,
  [data-mood='synthesis'] .blob.core {
    opacity: 0.36;
  }

  .vignette {
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at center, transparent 55%, rgb(0 0 0 / 0.45) 100%);
  }

  @keyframes drift-a {
    to {
      transform: translate(14vmax, 10vmax) scale(1.15);
    }
  }

  @keyframes drift-b {
    to {
      transform: translate(-12vmax, -8vmax) scale(1.1);
    }
  }

  @keyframes drift-c {
    to {
      transform: translate(-6vmax, 6vmax) scale(1.2);
    }
  }
</style>
