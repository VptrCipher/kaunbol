// Mic recording -> 16 kHz mono WAV, plus optional live speech-to-text.
(function () {
  async function toWav16k(blob) {
    const buf = await blob.arrayBuffer();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const decoded = await ctx.decodeAudioData(buf);
    ctx.close();
    const rate = 16000;
    const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const out = await off.startRendering();
    const pcm = out.getChannelData(0);
    const view = new DataView(new ArrayBuffer(44 + pcm.length * 2));
    const w = (o, s) => [...s].forEach((c, i) => view.setUint8(o + i, c.charCodeAt(0)));
    w(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE");
    w(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); w(36, "data"); view.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) {
      const s = Math.max(-1, Math.min(1, pcm[i]));
      view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return { blob: new Blob([view], { type: "audio/wav" }), seconds: decoded.duration };
  }

  async function durationOf(file) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const d = await ctx.decodeAudioData(await file.arrayBuffer());
      ctx.close();
      return d.duration;
    } catch { return null; }
  }

  class Recorder {
    constructor({ onTick, onTranscript, onLevel, onSpeechError }) {
      Object.assign(this, { onTick, onTranscript, onLevel, onSpeechError });
    }
    async start({ lang, transcribe }) {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false } });
      this.chunks = [];
      this.rec = new MediaRecorder(this.stream);
      this.rec.ondataavailable = (e) => e.data.size && this.chunks.push(e.data);
      this.rec.start(250);
      this.t0 = Date.now();
      this.timer = setInterval(() => this.onTick?.((Date.now() - this.t0) / 1000), 200);
      // level meter
      this.actx = new AudioContext();
      const an = this.actx.createAnalyser();
      an.fftSize = 512;
      this.actx.createMediaStreamSource(this.stream).connect(an);
      const data = new Uint8Array(an.fftSize);
      const loop = () => {
        an.getByteTimeDomainData(data);
        let peak = 0;
        for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
        this.onLevel?.(peak / 128);
        this.raf = requestAnimationFrame(loop);
      };
      loop();
      // live transcript
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      this.finalText = "";
      if (transcribe && SR) {
        this.sr = new SR();
        this.sr.lang = { en: "en-IN", hi: "hi-IN", kn: "kn-IN" }[lang] || "en-IN";
        this.sr.continuous = true;
        this.sr.interimResults = true;
        this.sr.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const r = e.results[i];
            if (r.isFinal) this.finalText += r[0].transcript + " ";
            else interim += r[0].transcript;
          }
          this.onTranscript?.((this.finalText + interim).trim());
        };
        let unavailable = false;
        this.sr.onerror = (event) => {
          unavailable = ["language-not-supported", "not-allowed", "service-not-allowed", "network"].includes(event.error);
          if (unavailable) this.onSpeechError?.(event.error);
        };
        this.sr.onend = () => { if (!unavailable && this.rec?.state === "recording") try { this.sr.start(); } catch {} };
        try { this.sr.start(); } catch {}
      }
    }
    async stop() {
      clearInterval(this.timer);
      cancelAnimationFrame(this.raf);
      const done = new Promise((r) => (this.rec.onstop = r));
      this.rec.stop();
      await done;
      try { this.sr?.stop(); } catch {}
      this.sr = null;
      this.stream.getTracks().forEach((t) => t.stop());
      this.actx.close();
      const raw = new Blob(this.chunks, { type: this.rec.mimeType });
      return toWav16k(raw);
    }
  }

  const INPUT_SIGNAL_LEVEL = 0.003;
  function openLiveInput(deviceId) {
    return navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false, noiseSuppression: false, autoGainControl: !deviceId,
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      },
    });
  }

  async function openPCAudio() {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: { displaySurface: "monitor" },
      audio: true,
      systemAudio: "include",
    });
    if (!stream.getAudioTracks().length) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error("pc_audio_missing");
    }
    return stream;
  }

  function inputLevel(samples) {
    let peak = 0, energy = 0;
    for (const value of samples) {
      peak = Math.max(peak, Math.abs(value));
      energy += value * value;
    }
    return { peak, rms: Math.sqrt(energy / (samples.length || 1)) };
  }

  class InputPreview {
    async start(deviceId, onLevel) {
      const stream = await openLiveInput(deviceId);
      if (this.stopped) { stream.getTracks().forEach((track) => track.stop()); return; }
      this.stream = stream;
      try {
        this.context = new AudioContext();
        const analyzer = this.context.createAnalyser();
        this.context.createMediaStreamSource(stream).connect(analyzer);
        const samples = new Float32Array(analyzer.fftSize);
        await this.context.resume();
        const tick = () => {
          if (this.stopped) return;
          analyzer.getFloatTimeDomainData(samples);
          onLevel(inputLevel(samples));
          this.frame = requestAnimationFrame(tick);
        };
        tick();
      } catch (error) { this.stop(); throw error; }
    }

    stop() {
      this.stopped = true;
      cancelAnimationFrame(this.frame);
      this.stream?.getTracks().forEach((track) => track.stop());
      this.context?.close().catch(() => {});
    }
  }

  // ---------------------------------------------------------- live streaming
  // Emits 100 ms frames of 16 kHz mono PCM16 (3200 bytes), the format the
  // DetectifAI stream expects for encoding=pcm_s16le&sample_rate=16000.
  const FRAME = 1600;
  const toPcm16 = (f32) => {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      const s = Math.max(-1, Math.min(1, f32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out.buffer;
  };

  const WORKLET = `class Tap extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array(${FRAME});this.n=0}
    process(i){const c=i[0]&&i[0][0];if(c){for(let k=0;k<c.length;k++){this.b[this.n++]=c[k];if(this.n===${FRAME}){this.port.postMessage(this.b.slice());this.n=0}}}return true}}
    registerProcessor('kb-tap',Tap);`;

  class LiveSource {
    constructor({ onFrame, onLevel, onTranscript, onEnded }) {
      Object.assign(this, { onFrame, onLevel, onTranscript, onEnded });
    }
    _level(f32) {
      const { peak, rms } = inputLevel(f32);
      this.onLevel?.(peak, rms);
    }
    async startStream(stream) {
      if (this.stopped) { stream.getTracks().forEach((track) => track.stop()); return; }
      this.stream = stream;
      this.stream.getTracks().forEach((track) => track.addEventListener("ended", () => this.onEnded?.(), { once: true }));
      this.ctx = new AudioContext({ sampleRate: 16000 });
      const worklet = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
      const context = this.ctx;
      try { await context.audioWorklet.addModule(worklet); }
      finally { URL.revokeObjectURL(worklet); }
      if (this.stopped) return;
      const node = new AudioWorkletNode(this.ctx, "kb-tap");
      node.port.onmessage = (e) => { this._level(e.data); this.onFrame(toPcm16(e.data)); };
      this.ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks())).connect(node);
      // Keep the worklet in the rendering graph. Its output is silent.
      node.connect(this.ctx.destination);
    }
    async startMic({ lang, transcribe, deviceId }) {
      await this.startStream(await openLiveInput(deviceId));
      if (this.stopped) return;
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (transcribe && SR) {
        this.finalText = "";
        this.sr = new SR();
        this.sr.lang = { en: "en-IN", hi: "hi-IN", kn: "kn-IN" }[lang] || "en-IN";
        this.sr.continuous = true;
        this.sr.interimResults = true;
        this.sr.onresult = (e) => {
          let interim = "";
          for (let i = e.resultIndex; i < e.results.length; i++) {
            const r = e.results[i];
            if (r.isFinal) this.finalText += r[0].transcript + " ";
            else interim += r[0].transcript;
          }
          this.onTranscript?.((this.finalText + interim).trim());
        };
        this.sr.onend = () => { if (this.ctx) try { this.sr.start(); } catch {} };
        try { this.sr.start(); } catch {}
      }
    }
    // Plays a recording out loud and streams it in real time, as if it were a call.
    async startFile(file) {
      const raw = await file.arrayBuffer();
      const tmp = new AudioContext();
      const decoded = await tmp.decodeAudioData(raw.slice(0));
      const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
      const s = off.createBufferSource();
      s.buffer = decoded; s.connect(off.destination); s.start();
      const pcm = (await off.startRendering()).getChannelData(0);
      this.ctx = tmp;
      const play = tmp.createBufferSource();
      play.buffer = decoded; play.connect(tmp.destination); play.start();
      let pos = 0;
      this.timer = setInterval(() => {
        if (pos >= pcm.length) { clearInterval(this.timer); this.onEnded?.(); return; }
        const chunk = pcm.subarray(pos, pos + FRAME);
        pos += FRAME;
        this._level(chunk);
        this.onFrame(toPcm16(chunk));
      }, 100);
      return decoded.duration;
    }
    stop() {
      if (this.stopped) return;
      this.stopped = true;
      clearInterval(this.timer);
      try { this.sr?.stop(); } catch {}
      this.sr = null;
      this.stream?.getTracks().forEach((t) => t.stop());
      this.ctx?.close();
      this.ctx = null;
    }
  }

  window.KBAudio = { Recorder, LiveSource, InputPreview, INPUT_SIGNAL_LEVEL, durationOf,
    openPCAudio, speechSupported: !!(window.SpeechRecognition || window.webkitSpeechRecognition) };
})();
