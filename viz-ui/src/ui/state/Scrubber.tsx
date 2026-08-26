interface ScrubberProps {
  step: number;
  maxStep: number;
  onChange: (step: number) => void;
}

/** World-over-time playback: drag from "seed" through every committed step.
 * Layout stays fixed while scrubbing — facts appear, nothing jumps. */
export function Scrubber({ step, maxStep, onChange }: ScrubberProps) {
  return (
    <div className="scrubber">
      <label htmlFor="step-scrubber">
        {step === 0 ? "as seeded" : step === maxStep ? `step ${step} (latest)` : `after step ${step}`}
      </label>
      <input
        id="step-scrubber"
        type="range"
        min={0}
        max={maxStep}
        step={1}
        value={step}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="scrub-ticks">
        <span>seed</span>
        <span>{maxStep} steps</span>
      </span>
    </div>
  );
}
