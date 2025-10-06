class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const { targetSampleRate = 16000, chunkMs = 320 } = options.processorOptions || {};
    this.inputSampleRate = sampleRate;
    this.targetSampleRate = targetSampleRate;
    this.chunkSamples = Math.floor((targetSampleRate * chunkMs) / 1000);
    this.buffer = new Int16Array(this.chunkSamples * 4);
    this.writePos = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const channel = input[0];
    const resampled = this.resample(channel);
    this.enqueue(resampled);
    return true;
  }

  resample(frame) {
    const ratio = this.targetSampleRate / this.inputSampleRate;
    const length = Math.floor(frame.length * ratio);
    const output = new Int16Array(length);
    let lastSample = frame[frame.length - 1] || 0;
    for (let i = 0; i < length; i++) {
      const index = i / ratio;
      const i0 = Math.floor(index);
      const i1 = Math.min(i0 + 1, frame.length - 1);
      const frac = index - i0;
      const sample = frame[i0] + (frame[i1] - frame[i0]) * frac;
      lastSample = sample;
      output[i] = Math.max(-1, Math.min(1, lastSample)) * 0x7fff;
    }
    return output;
  }

  enqueue(samples) {
    this.ensureCapacity(samples.length);
    this.buffer.set(samples, this.writePos);
    this.writePos += samples.length;
    while (this.writePos >= this.chunkSamples) {
      const chunk = this.buffer.slice(0, this.chunkSamples);
      const remainder = this.buffer.subarray(this.chunkSamples, this.writePos);
      this.buffer.set(remainder, 0);
      this.writePos -= this.chunkSamples;
      this.port.postMessage(chunk.buffer, [chunk.buffer]);
    }
  }

  ensureCapacity(additional) {
    if (this.writePos + additional <= this.buffer.length) return;
    const next = new Int16Array((this.buffer.length + additional) * 2);
    next.set(this.buffer.subarray(0, this.writePos));
    this.buffer = next;
  }
}

registerProcessor("pcm-processor", PCMWorkletProcessor);
