"use strict";

// Shared by the single-image console and the storyboard editor.
export function createImageSettings(root) {
return {
  normalizeSize(value) {
    return value.toLowerCase().replace(/\s+/g, "").replace(/[×*]/g, "x");
  },
  validSize(value) {
    return /^[1-9]\d{1,4}x[1-9]\d{1,4}$/.test(this.normalizeSize(value));
  },
  describeSize(value) {
    const size = this.normalizeSize(value);
    if (!this.validSize(size)) return "输入宽 × 高，例如 1920x1080";
    const [width, height] = size.split("x").map(Number);
    const gcd = (a, b) => b ? gcd(b, a % b) : a;
    const divisor = gcd(width, height);
    let ratio = `${width / divisor}:${height / divisor}`;
    ratio = ({ "7:3": "21:9", "3:7": "9:21", "8:5": "16:10", "5:8": "10:16" })[ratio] || ratio;
    return `${ratio} · ${width} × ${height}`;
  },
  init(config) {
    const models = [...new Set([config.default_model, ...(config.suggested_models || [])].filter(Boolean))];
    root.getElementById("modelOptions").replaceChildren(...models.map((value) => new Option(value, value)));
    const presets = config.size_presets || (config.suggested_sizes || []).map((size) => ({ size, label: this.describeSize(size) }));
    const select = root.getElementById("sizePreset");
    select.replaceChildren(new Option("自定义分辨率", ""), ...presets.map((item) => new Option(item.label, item.size)));
    const input = root.getElementById("size");
    input.addEventListener("input", () => this.syncSize());
    select.addEventListener("change", () => {
      if (select.value) {
        input.value = select.value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        input.focus(); input.select();
      }
    });
  },
  syncSize() {
    const input = root.getElementById("size"), select = root.getElementById("sizePreset");
    const size = this.normalizeSize(input.value);
    select.value = Array.from(select.options).some((option) => option.value === size) ? size : "";
    select.disabled = input.disabled;
    root.getElementById("sizeInfo").textContent = this.describeSize(size);
  },
};
}
