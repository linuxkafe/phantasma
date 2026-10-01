# Screenshots

Capture these for the README:

## Desktop (≥900px)
```bash
# In headless Chromium at 1280x720
playwright screenshot --device="Desktop Chrome" --url="http://localhost:5000/" --output=docs/screenshots/desktop.png
```

## Mobile (375×667)
```bash
# In headless Chromium at 375x667 (iPhone SE)
playwright screenshot --device="iPhone SE" --url="http://localhost:5000/" --output=docs/screenshots/mobile.png
```

## Required Views
- [ ] Desktop: full page with device tiles + conversation
- [ ] Mobile: device strip with rooms stacked
- [ ] Mobile: dock closed (bottom bar)
- [ ] Mobile: dock open (conversation panel)
- [ ] Mobile: burger menu open
- [ ] Mobile: voice composer with mic button

## Automation
Add to CI: `make test-mobile` runs Playwright tests that validate layout programmatically (no screenshots needed for gate).