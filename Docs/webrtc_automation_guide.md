# Guide: Automating Amazon Connect WebRTC (In-App Calling)

You can automate Amazon Connect WebRTC calls in a CI/CD pipeline using a headless browser (like Chrome via Playwright or Puppeteer) with fake audio streams.

## Prerequisites
1.  **Node.js Environment** (for Playwright/Puppeteer).
2.  **FFmpeg** (to generate audio files for injection).
3.  **Amazon Connect Instance** configured for In-App Calling.
4.  **JWT Token Generation Logic** (Backend API or Lambda).

## Implementation Steps

### 1. Configure the Browser (Headless Chrome)
Launch Chrome with specific flags to allow fake media devices without a physical microphone/camera.

```javascript
// playwright.config.js or test setup
const browser = await chromium.launch({
  args: [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    '--use-file-for-fake-audio-capture=./test-audio/hello_world.wav', // Inject this audio as mic input
    '--allow-file-access-from-files',
    '--headless=new'
  ]
});
```

### 2. Prepare Audio Files
Create `.wav` files for your test scenarios (e.g., "I want to check my balance").
*   Format: **16-bit PCM WAV, mono, 44.1kHz**.
*   Place these files in your test repository.

### 3. Build the Test Script (Playwright Example)

```javascript
const { test, expect } = require('@playwright/test');

test('Amazon Connect WebRTC Call', async ({ page }) => {
  // 1. Navigate to your app (or a test page that uses Connect Streams API)
  await page.goto('https://your-app.com/connect-test');

  // 2. Authenticate & Initialize CCP
  // (You might need to mock your backend API to return a valid JWT)
  await page.evaluate(() => {
    // Assuming you have a function to init streams
    initCCP(jwtToken);
  });

  // 3. Start the Call
  await page.click('#start-call-button');

  // 4. Validate Connection State
  await expect(page.locator('#connection-status')).toHaveText('Connected');

  // 5. Validate Audio Transmission (Using WebRTC Stats)
  // We can't "hear" the agent easily, but we can check if packets are flowing.
  const audioPackets = await page.evaluate(async () => {
    const pc = window.connect.core.getAgent().getContacts()[0].getAgentConnection().getMediaController().peerConnection;
    const stats = await pc.getStats();
    let packets = 0;
    stats.forEach(report => {
      if (report.type === 'inbound-rtp' && report.kind === 'audio') {
        packets = report.packetsReceived;
      }
    });
    return packets;
  });

  expect(audioPackets).toBeGreaterThan(0);

  // 6. End Call
  await page.click('#end-call-button');
});
```

### 4. Handling Agent Audio (The Tricky Part)
In a headless environment, you cannot "verify" what the agent (IVR) said (e.g., "Welcome to Bank").
*   **Workaround:** Use the **Amazon Connect Contact Lens** API (like in your Chime tests) to fetch the transcript *after* the call completes.
*   **Real-time:** Extremely difficult. Requires capturing the browser's audio output stream and sending it to a speech-to-text service, which is flaky in CI.

## CI/CD Pipeline Integration (GitHub Actions)

```yaml
name: WebRTC Tests
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npm test
```

## Summary
*   **Feasible:** Yes.
*   **Recommended:** Only if you specifically need to test the *Client Application's* ability to connect.
*   **For IVR Logic:** The **Chime SMA approach** is superior because it gives you programmable control over the audio stream (send/receive) without browser flakiness.
