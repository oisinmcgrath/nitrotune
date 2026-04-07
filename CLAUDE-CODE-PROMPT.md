Read v1-analysis.md in this repo thoroughly — it contains the full hardware spec,
EC register mappings, v1 performance data (860 readings over 29 minutes), and
detailed v2 requirements.

Also read the current nitro-fan-control.sh to understand the v1 implementation.

The full v1 test log is at /var/log/nitro-fan-control.log on this machine.

Build v2 of nitro-fan-control.sh with these key changes:

1. SMOOTH RAMPING: Instead of instantly jumping to the target fan speed,
   limit changes to +3% per cycle (ramping up) and -2% per cycle (ramping down).
   This is the most important change — it eliminates jarring audible fan jumps.

2. LINEAR INTERPOLATION: Instead of a stepped curve with discrete thresholds,
   interpolate linearly between curve points so fan speed changes gradually
   with temperature.

3. Higher baseline of 25% minimum fan speed.

4. Keep polling at 2 seconds, keep 3°C hysteresis.

5. Add a VERBOSE config variable — when true, log every reading; when false,
   log every 30 seconds. Set to true for testing.

6. Log format should show both the curve target AND the actual ramped speed
   being sent to the EC, so we can verify smooth ramping is working.

After editing, deploy with:
  sudo cp nitro-fan-control.sh /usr/local/bin/
  sudo cp nitro-fan-control.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl restart nitro-fan-control

Then watch: tail -f /var/log/nitro-fan-control.log

Verify that fan speed changes by small increments (1-3%) per reading,
not in large jumps.
