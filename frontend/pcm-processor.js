/**
 * AudioWorklet processor — captures mic PCM and posts it to the main thread.
 * Runs on a dedicated audio-rendering thread (off the UI thread).
 * This MUST live in its own file, loaded via audioWorklet.addModule().
 */
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._active = true;
    this.port.onmessage = (e) => {
      if (e.data === "stop") this._active = false;
    };
  }

  process(inputs) {
    if (!this._active) return false;

    const input = inputs[0];
    if (!input || !input[0] || input[0].length === 0) return true;

    const float32 = input[0]; // mono channel

    // Convert Float32 → Int16 PCM
    const pcm16 = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    // Post raw bytes to main thread
    this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);