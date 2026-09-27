// STUB — replaced by the visuals implementation (three.js scene).
import type { CreateScene, SceneController } from './types';

export const createScene: CreateScene = async () => {
  const noop: SceneController = {
    setMood: () => {},
    pulse: () => {},
    setActive: () => {},
    setAgreement: () => {},
    setQuality: () => {},
    setPaused: () => {},
    dispose: () => {},
  };
  return noop;
};
