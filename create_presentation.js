/**
 * Amazon Connect Testing Framework – Presentation Generator
 * Uses PptxGenJS to build a polished 11-slide deck with speaker notes.
 *
 * Run: node create_presentation.js
 * Output: amazon_connect_testing_overview.pptx
 */

const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");

// Icon imports
const {
  FaMicrophone, FaFlask, FaRobot, FaCodeBranch, FaCogs,
  FaCloudUploadAlt, FaCheckCircle, FaPlayCircle, FaDatabase,
  FaDotCircle, FaBolt, FaTerminal, FaShieldAlt, FaStream
} = require("react-icons/fa");
const { MdCallEnd, MdDoneAll, MdLoop } = require("react-icons/md");

// ─── Color Palette (Ocean Gradient theme, AWS-inspired) ─────────────────────
const C = {
  darkBg:   "021526",   // Very dark navy – title & closing slides
  primary:  "065A82",   // Deep ocean blue
  secondary:"1C7293",   // Teal blue
  accent:   "02C39A",   // Vivid mint green
  accentAlt:"F0A500",   // Amber – warm contrast accent
  lightBg:  "EBF5FB",   // Ice blue – content slide backgrounds
  white:    "FFFFFF",
  darkText: "0A2342",   // Near-black navy
  midText:  "2C5F7A",   // Medium blue-grey
  faint:    "B8D4E8",   // Soft blue for captions/lines
  cardBg:   "FFFFFF",   // Card backgrounds on light slides
  cardShadow:"000000",
  tealLight:"D0EEF5",   // Light teal for table header fills
};

// ─── Icon helper ─────────────────────────────────────────────────────────────
function svgToPng(iconComponent, hexColor, size = 256) {
  const svg = ReactDOMServer.renderToStaticMarkup(
    React.createElement(iconComponent, { color: `#${hexColor}`, size: String(size) })
  );
  return sharp(Buffer.from(svg)).png().toBuffer();
}

async function iconBase64(iconComponent, hexColor, size = 256) {
  const buf = await svgToPng(iconComponent, hexColor, size);
  return "image/png;base64," + buf.toString("base64");
}

// ─── Reusable helpers ────────────────────────────────────────────────────────
function makeShadow() {
  return { type: "outer", color: C.cardShadow, blur: 8, offset: 3, angle: 135, opacity: 0.12 };
}

function addSlideHeader(slide, titleText, subtitle = null) {
  // Top accent bar
  slide.addShape("rect", { x: 0, y: 0, w: 10, h: 0.55, fill: { color: C.primary } });
  // Title text
  slide.addText(titleText, {
    x: 0.45, y: 0.07, w: 8.5, h: 0.42,
    fontSize: 22, bold: true, color: C.white, fontFace: "Calibri", margin: 0
  });
  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.45, y: 0.58, w: 9.1, h: 0.32,
      fontSize: 11, italic: true, color: C.midText, fontFace: "Calibri", margin: 0
    });
  }
}

function addFooter(slide) {
  slide.addShape("rect", { x: 0, y: 5.35, w: 10, h: 0.275, fill: { color: C.primary } });
  slide.addText("Amazon Connect Automation Testing Framework  |  Confidential", {
    x: 0.5, y: 5.36, w: 8, h: 0.25,
    fontSize: 8, color: C.faint, fontFace: "Calibri", margin: 0
  });
}

// ─── Main ─────────────────────────────────────────────────────────────────────
async function buildPresentation() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.author = "Amazon Connect Testing Team";
  pres.title = "Amazon Connect Automation Testing Framework";
  pres.subject = "Automated testing suite for voice flows, Lambda, and Lex bots";

  // Pre-render icons
  const iconCheck   = await iconBase64(FaCheckCircle,    C.accent);
  const iconMic     = await iconBase64(FaMicrophone,     C.white);
  const iconFlask   = await iconBase64(FaFlask,          C.white);
  const iconRobot   = await iconBase64(FaRobot,          C.white);
  const iconCI      = await iconBase64(FaCodeBranch,     C.white);
  const iconCogs    = await iconBase64(FaCogs,           C.white);
  const iconCloud   = await iconBase64(FaCloudUploadAlt, C.white);
  const iconPlay    = await iconBase64(FaPlayCircle,     C.accent);
  const iconDB      = await iconBase64(FaDatabase,       C.accent);
  const iconShield  = await iconBase64(FaShieldAlt,      C.accent);
  const iconBolt    = await iconBase64(FaBolt,           C.accentAlt);
  const iconTerm    = await iconBase64(FaTerminal,       C.accent);
  const iconStream  = await iconBase64(FaStream,         C.accent);
  const iconMicDark = await iconBase64(FaMicrophone,     C.primary);
  const iconLoop    = await iconBase64(MdLoop,           C.accent);

  // ===========================================================================
  // SLIDE 1 – TITLE
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.darkBg };

    // Left accent bar
    s.addShape("rect", { x: 0, y: 0, w: 0.22, h: 5.625, fill: { color: C.accent } });

    // Tagline ribbon – moved down to give 0.3" gap from title text bottom
    s.addShape("rect", { x: 0.22, y: 2.18, w: 9.78, h: 0.45, fill: { color: C.primary } });
    s.addText("AWS  ·  boto3  ·  pytest  ·  LocalStack  ·  Chime SDK  ·  Lex V2", {
      x: 0.5, y: 2.20, w: 9.3, h: 0.38,
      fontSize: 10, color: C.faint, fontFace: "Calibri", italic: true, margin: 0
    });

    // Main title
    s.addText("Amazon Connect", {
      x: 0.5, y: 0.55, w: 9.2, h: 0.68,
      fontSize: 46, bold: true, color: C.white, fontFace: "Calibri", margin: 0
    });
    s.addText("Voice Flow Automation", {
      x: 0.5, y: 1.22, w: 9.2, h: 0.68,
      fontSize: 38, bold: false, color: C.faint, fontFace: "Calibri", margin: 0
    });

    // Subtitle – sits below ribbon
    s.addText("A production-ready Python testing framework for\ncontact centre flows, Lambda functions, and Lex bots", {
      x: 0.5, y: 2.78, w: 6.5, h: 0.9,
      fontSize: 14, color: C.faint, fontFace: "Calibri", lineSpacingMultiple: 1.3,
      margin: 0
    });

    // Three feature badges – wider pitch to avoid touching
    const badges = ["Voice Flow Testing", "Lambda Regression", "Lex Bot NLU"];
    badges.forEach((b, i) => {
      s.addShape("rect", {
        x: 0.5 + i * 2.72, y: 3.98, w: 2.38, h: 0.52,
        fill: { color: C.secondary }, rectRadius: 0.08, shadow: makeShadow()
      });
      s.addText(b, {
        x: 0.5 + i * 2.72, y: 3.98, w: 2.38, h: 0.52,
        fontSize: 11, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", valign: "middle", margin: 0
      });
    });

    // Date
    s.addText("February 2026", {
      x: 0.5, y: 4.78, w: 4, h: 0.28,
      fontSize: 9, color: C.faint, fontFace: "Calibri", italic: true, margin: 0
    });

    s.addNotes(`Welcome everyone. This presentation covers the Amazon Connect Voice Flow Automation repository — a comprehensive end-to-end testing framework for AWS contact centre solutions.

The framework has three major pillars: voice flow testing driven by the Chime SDK, Lambda regression testing using LocalStack, and Lex V2 bot NLU testing. We'll walk through each module, explain how it works, and show how everything ties together in CI/CD.`);
  }

  // ===========================================================================
  // SLIDE 2 – AGENDA
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "Agenda");
    addFooter(s);

    const items = [
      { icon: iconMicDark, label: "1  ·  What Is This Framework?",    desc: "Purpose, scope, and key capabilities" },
      { icon: iconMicDark, label: "2  ·  Architecture Deep Dive",     desc: "How Chime SDK drives Amazon Connect calls" },
      { icon: iconMicDark, label: "3  ·  Voice Flow Testing",         desc: "DTMF scripting, CTR validation, CloudWatch" },
      { icon: iconMicDark, label: "4  ·  Lambda & LocalStack Testing", desc: "Containerised regression without cloud costs" },
      { icon: iconMicDark, label: "5  ·  Lex Bot NLU Testing",        desc: "Multi-turn conversations, intent & slot validation" },
    ];

    // Render agenda icons and text
    const agendaIcons = [iconPlay, iconStream, iconMicDark, iconFlask, iconRobot];
    items.forEach((item, i) => {
      const y = 0.85 + i * 0.87;
      // Card bg
      s.addShape("rect", {
        x: 0.4, y: y, w: 9.2, h: 0.72,
        fill: { color: C.white }, shadow: makeShadow()
      });
      // Left color accent
      s.addShape("rect", {
        x: 0.4, y: y, w: 0.2, h: 0.72,
        fill: { color: i < 2 ? C.primary : i < 4 ? C.secondary : C.accent }
      });
      // Icon circle
      s.addShape("oval", {
        x: 0.72, y: y + 0.11, w: 0.5, h: 0.5,
        fill: { color: i < 2 ? C.primary : i < 4 ? C.secondary : C.accent }
      });
      s.addImage({ data: agendaIcons[i], x: 0.77, y: y + 0.16, w: 0.4, h: 0.4 });
      // Text
      s.addText(item.label, {
        x: 1.35, y: y + 0.07, w: 7.8, h: 0.28,
        fontSize: 13, bold: true, color: C.darkText, fontFace: "Calibri", margin: 0
      });
      s.addText(item.desc, {
        x: 1.35, y: y + 0.37, w: 7.8, h: 0.24,
        fontSize: 10, color: C.midText, fontFace: "Calibri", italic: true, margin: 0
      });
    });

    s.addNotes(`Today's agenda covers five areas:
1. An overview of what this framework does and why it was built.
2. The underlying architecture — specifically how we use AWS Chime SDK to simulate real customer calls into Amazon Connect.
3. A deep dive into voice flow testing — including DTMF scripts, Contact Trace Record (CTR) validation, and CloudWatch log verification.
4. Lambda regression testing with LocalStack, which lets us run full AWS service integrations locally in Docker.
5. Lex bot NLU testing — multi-turn conversation flows, intent routing, and slot filling.`);
  }

  // ===========================================================================
  // SLIDE 3 – WHAT IS THIS FRAMEWORK?
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "What Is This Framework?", "Automated end-to-end quality assurance for AWS contact centre solutions");
    addFooter(s);

    // Left column – overview text
    const leftItems = [
      { icon: iconPlay,   title: "Automated Voice Testing",      body: "Simulates real PSTN customer calls via AWS Chime SDK SIP Media Application and validates routing outcomes in Amazon Connect." },
      { icon: iconDB,     title: "Configurable Test Cases",       body: "All test scenarios are defined in JSON — no code changes required to add new call flows, DTMF paths, or expected queues." },
      { icon: iconShield, title: "Deep Flow Validation",          body: "Verifies contact attributes, CTR records, CloudWatch logs, and DynamoDB state — not just whether the call connected." },
    ];
    leftItems.forEach((item, i) => {
      const y = 0.95 + i * 1.35;
      s.addImage({ data: item.icon, x: 0.45, y: y + 0.05, w: 0.38, h: 0.38 });
      s.addText(item.title, {
        x: 0.95, y: y, w: 4.55, h: 0.32,
        fontSize: 13, bold: true, color: C.darkText, fontFace: "Calibri", margin: 0
      });
      s.addText(item.body, {
        x: 0.95, y: y + 0.33, w: 4.55, h: 0.75,
        fontSize: 10.5, color: C.midText, fontFace: "Calibri", lineSpacingMultiple: 1.3, margin: 0
      });
    });

    // Right column – key stats cards
    const stats = [
      { value: "3",     label: "Testing Modules",     sub: "Voice · Lambda · Lex" },
      { value: "15+",   label: "Voice Test Cases",    sub: "DTMF, multi-turn, error paths" },
      { value: "100%",  label: "Local Execution",     sub: "MOCK_AWS mode — no cloud costs" },
    ];
    stats.forEach((stat, i) => {
      const y = 0.98 + i * 1.35;
      s.addShape("rect", {
        x: 5.8, y: y, w: 3.75, h: 1.1,
        fill: { color: i === 0 ? C.primary : i === 1 ? C.secondary : C.accent },
        shadow: makeShadow()
      });
      s.addText(stat.value, {
        x: 5.8, y: y + 0.05, w: 3.75, h: 0.6,
        fontSize: 42, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", valign: "middle", margin: 0
      });
      s.addText(stat.label, {
        x: 5.8, y: y + 0.62, w: 3.75, h: 0.24,
        fontSize: 11, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0
      });
      s.addText(stat.sub, {
        x: 5.8, y: y + 0.85, w: 3.75, h: 0.2,
        fontSize: 8.5, color: C.faint, fontFace: "Calibri",
        align: "center", italic: true, margin: 0
      });
    });

    s.addNotes(`This framework was built to solve a common problem: teams build Amazon Connect contact flows but have no automated way to regression-test them.

Key differentiators:
• It tests the FULL call path — from a simulated customer call, through the PSTN, into Connect routing, all the way to queue assignment and database state.
• Test cases are pure JSON, so QA engineers with no Python experience can add new scenarios.
• The MOCK_AWS mode lets developers run the full suite locally without incurring AWS charges.
• Validation goes beyond "did the call connect" — we check Contact Trace Records, CloudWatch logs, contact attributes, and DynamoDB side-effects.`);
  }

  // ===========================================================================
  // SLIDE 4 – ARCHITECTURE
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.darkBg };
    // Top accent
    s.addShape("rect", { x: 0, y: 0, w: 10, h: 0.55, fill: { color: C.accent } });
    s.addText("Architecture: How The Framework Works", {
      x: 0.45, y: 0.08, w: 9, h: 0.4,
      fontSize: 22, bold: true, color: C.darkBg, fontFace: "Calibri", margin: 0
    });

    // Footer
    s.addShape("rect", { x: 0, y: 5.35, w: 10, h: 0.275, fill: { color: C.secondary } });
    s.addText("Amazon Connect Automation Testing Framework  |  Confidential", {
      x: 0.5, y: 5.36, w: 8, h: 0.25,
      fontSize: 8, color: C.faint, fontFace: "Calibri", margin: 0
    });

    // Flow boxes
    const steps = [
      { step: "01", label: "Test Runner",       sub: "pytest +\nJSON test cases",    color: C.accent },
      { step: "02", label: "Chime SDK",          sub: "SIP Media Application\n(outbound call)",  color: C.primary },
      { step: "03", label: "PSTN / Connect",     sub: "Amazon Connect\nphone number",    color: C.secondary },
      { step: "04", label: "Contact Flow",       sub: "DTMF routing,\nLambda dips",     color: C.primary },
      { step: "05", label: "Validation",         sub: "CTR · CloudWatch\nDynamoDB",      color: C.accent },
    ];

    steps.forEach((step, i) => {
      const x = 0.3 + i * 1.88;
      // Box – height reduced to 1.88 to eliminate dead space at bottom
      s.addShape("rect", {
        x: x, y: 0.78, w: 1.68, h: 1.88,
        fill: { color: "0D2137" }, shadow: makeShadow()
      });
      // Top color band
      s.addShape("rect", { x: x, y: 0.78, w: 1.68, h: 0.42, fill: { color: step.color } });
      // Step number
      s.addText(step.step, {
        x: x, y: 0.78, w: 1.68, h: 0.42,
        fontSize: 16, bold: true, color: C.darkBg, align: "center", valign: "middle",
        fontFace: "Calibri", margin: 0
      });
      // Label
      s.addText(step.label, {
        x: x + 0.1, y: 1.28, w: 1.48, h: 0.38,
        fontSize: 11, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0
      });
      // Sub text
      s.addText(step.sub, {
        x: x + 0.1, y: 1.68, w: 1.48, h: 0.74,
        fontSize: 9, color: C.faint, fontFace: "Calibri",
        align: "center", lineSpacingMultiple: 1.3, margin: 0
      });
      // Arrow – centred in the 0.20" inter-box gap
      if (i < 4) {
        s.addShape("rect", {
          x: x + 1.70, y: 1.6, w: 0.14, h: 0.08,
          fill: { color: C.faint }
        });
        s.addText("›", {
          x: x + 1.68, y: 1.48, w: 0.20, h: 0.34,
          fontSize: 18, color: C.accent, fontFace: "Calibri", align: "center",
          valign: "middle", bold: true, margin: 0
        });
      }
    });

    // Description blocks – start 0.35" below the flow boxes, more height
    const descItems = [
      "Test runner reads JSON test cases and uses Chime SDK's CreateSipMediaApplicationCall to place a real outbound SIP call.",
      "Chime Lambda handler executes the call script: TTS prompts, DTMF tones, and wait steps to drive the IVR.",
      "After the call, the framework polls Connect's CTR, CloudWatch Logs, and DynamoDB to validate every expected outcome.",
    ];
    descItems.forEach((d, i) => {
      s.addShape("rect", {
        x: 0.3 + i * 3.22, y: 2.85, w: 3.0, h: 2.22,
        fill: { color: "0D2137" }, shadow: makeShadow()
      });
      s.addText(d, {
        x: 0.42 + i * 3.22, y: 2.95, w: 2.76, h: 2.02,
        fontSize: 9.5, color: C.faint, fontFace: "Calibri",
        lineSpacingMultiple: 1.4, margin: 0
      });
    });

    s.addNotes(`Let's walk through the architecture step by step.

Step 1 — Test Runner: pytest reads the JSON test cases and orchestrates each test. It sets up DynamoDB state, configures pre-call attributes, and initiates the call.

Step 2 — Chime SDK: We use AWS Chime SIP Media Application to place a real outbound PSTN call to the Amazon Connect claimed phone number. A Lambda function handles the Chime call events and executes the call script — playing TTS, sending DTMF tones, and waiting for IVR prompts.

Step 3 — Amazon Connect: The call arrives as a real inbound call. The contact flow executes just as it would for a real customer.

Step 4 — Contact Flow: Routing logic runs — checking business hours, contact attributes, Lambda data dips, and DTMF choices.

Step 5 — Validation: After the call, we poll CloudWatch Logs (flow execution path), the CTR (contact trace record), and DynamoDB to verify every expected outcome. Hard failures are registered in pytest — there are no false greens.`);
  }

  // ===========================================================================
  // SLIDE 5 – VOICE FLOW TESTING
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "Voice Flow Testing", "End-to-end call simulation using AWS Chime SDK SIP Media Application");
    addFooter(s);

    // Left column – capabilities list
    const capabilities = [
      { icon: iconPlay,   text: "First-call & returning-caller scenarios — validates greetingPlayed attribute branching" },
      { icon: iconTerm,   text: "DTMF scripted menus — Press 1 (Queue), Press 2 (Secure Input), Press 3 (Lambda dip)" },
      { icon: iconLoop,   text: "Multi-turn & reprompt paths — simulates no-input, invalid input, and retry loops" },
      { icon: iconShield, text: "Transfer tracking — verifies sub-flow transfers (SampleSecureInput, SampleLambdaIntegration)" },
      { icon: iconDB,     text: "DynamoDB side-effect validation — seeded items cleaned up via pytest finalizer + TTL" },
      { icon: iconStream, text: "CloudWatch Logs — contact flow execution path verified via Logs Insights queries" },
    ];

    capabilities.forEach((cap, i) => {
      const y = 0.85 + i * 0.73;
      s.addShape("rect", {
        x: 0.4, y: y, w: 5.5, h: 0.62,
        fill: { color: C.white }, shadow: makeShadow()
      });
      s.addImage({ data: cap.icon, x: 0.56, y: y + 0.11, w: 0.38, h: 0.38 });
      s.addText(cap.text, {
        x: 1.07, y: y + 0.09, w: 4.65, h: 0.44,
        fontSize: 9.5, color: C.midText, fontFace: "Calibri",
        lineSpacingMultiple: 1.25, margin: 0
      });
    });

    // Right column – test case example card
    s.addShape("rect", { x: 6.15, y: 0.78, w: 3.45, h: 1.65, fill: { color: C.primary }, shadow: makeShadow() });
    s.addText("Sample Test Case", {
      x: 6.25, y: 0.82, w: 3.25, h: 0.3,
      fontSize: 11, bold: true, color: C.white, fontFace: "Calibri", margin: 0
    });
    s.addText('CF-E2E-001 · First Call Happy Path\n"Press 1, Join BasicQueue"\n\nExpects:\n  • greetingPlayed = true\n  • Queue = BasicQueue\n  • CTR status = Queued', {
      x: 6.25, y: 1.16, w: 3.25, h: 1.2,
      fontSize: 8.5, color: C.faint, fontFace: "Consolas",
      lineSpacingMultiple: 1.3, margin: 0
    });

    // Right column – poll timeouts card
    s.addShape("rect", { x: 6.15, y: 2.65, w: 3.45, h: 2.55, fill: { color: C.white }, shadow: makeShadow() });
    s.addShape("rect", { x: 6.15, y: 2.65, w: 3.45, h: 0.35, fill: { color: C.secondary } });
    s.addText("Poll Timeouts", {
      x: 6.25, y: 2.66, w: 3.25, h: 0.3,
      fontSize: 11, bold: true, color: C.white, fontFace: "Calibri", margin: 0
    });
    const timeouts = [
      ["Queue check",   "60s",  "5s poll"],
      ["CTR record",    "300s", "10s poll"],
      ["CloudWatch",    "120s", "5s poll"],
      ["Transcribe",    "300s", "15s poll"],
    ];
    timeouts.forEach(([label, total, interval], i) => {
      const y = 3.08 + i * 0.48;
      s.addText(label, {
        x: 6.25, y: y, w: 1.4, h: 0.3,
        fontSize: 9, color: C.darkText, fontFace: "Calibri", margin: 0
      });
      s.addText(total, {
        x: 7.7, y: y, w: 0.8, h: 0.3,
        fontSize: 9, bold: true, color: C.primary, fontFace: "Calibri", margin: 0
      });
      s.addText(interval, {
        x: 8.55, y: y, w: 0.9, h: 0.3,
        fontSize: 8.5, color: C.midText, fontFace: "Calibri", italic: true, margin: 0
      });
    });

    s.addNotes(`Voice flow testing is the heart of this framework.

Each test case defines:
- A destination phone number (your Amazon Connect claimed number)
- A call script: a sequence of wait, DTMF, and speak actions
- Expected outcomes: queue, contact attributes, and behavior type

The greetingPlayed attribute pattern is a real production use case — first-time callers hear a welcome message; returning callers skip straight to the menu. We test both branches.

The poll timeouts are carefully tuned: CTR records can take 1-3 minutes to index in Connect, so we poll for up to 5 minutes. CloudWatch Logs Insights queries are run asynchronously.

Every test has a pytest finalizer that tears down DynamoDB state to keep tests isolated and prevent cross-test pollution.`);
  }

  // ===========================================================================
  // SLIDE 6 – LAMBDA TESTING
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "Lambda Regression Testing · LocalStack", "Full AWS service emulation in Docker — no cloud credentials required");
    addFooter(s);

    // Left – process flow
    const steps6 = [
      { icon: iconCloud, color: C.primary,   label: "1 · Spin Up LocalStack",      body: "Testcontainers spins up LocalStack Docker image. Lambda, S3, DynamoDB emulated locally." },
      { icon: iconCogs,  color: C.secondary, label: "2 · Deploy & Configure",       body: "ZIP-packages sample_lambda.py, creates functions, S3 buckets & DynamoDB tables per test case setup block." },
      { icon: iconBolt,  color: C.accentAlt, label: "3 · Invoke & Validate",        body: "Sends trigger_event payload → checks statusCode, response body, DynamoDB items, S3 objects, and FunctionError payloads." },
      { icon: iconCheck, color: C.accent,    label: "4 · HTML + JSON Report",       body: "pytest-html generates a rich visual report; conftest.py emits a structured JSON result for CI artefact upload." },
    ];

    steps6.forEach((step, i) => {
      const y = 0.88 + i * 1.08;
      s.addShape("rect", {
        x: 0.4, y: y, w: 5.5, h: 0.9,
        fill: { color: C.white }, shadow: makeShadow()
      });
      s.addShape("rect", { x: 0.4, y: y, w: 0.18, h: 0.9, fill: { color: step.color } });
      s.addShape("oval", { x: 0.68, y: y + 0.18, w: 0.54, h: 0.54, fill: { color: step.color } });
      s.addImage({ data: step.icon, x: 0.73, y: y + 0.23, w: 0.44, h: 0.44 });
      s.addText(step.label, {
        x: 1.35, y: y + 0.07, w: 4.35, h: 0.3,
        fontSize: 12, bold: true, color: C.darkText, fontFace: "Calibri", margin: 0
      });
      s.addText(step.body, {
        x: 1.35, y: y + 0.4, w: 4.35, h: 0.45,
        fontSize: 9.5, color: C.midText, fontFace: "Calibri",
        lineSpacingMultiple: 1.25, margin: 0
      });
    });

    // Right – stats
    const lambdaStats = [
      { v: "TC-001", l: "S3 → DynamoDB",       s: "event trigger" },
      { v: "TC-002", l: "DynamoDB streams",      s: "error path" },
      { v: "TC-003", l: "Async invocation",      s: "side-effect check" },
      { v: "0",      l: "Cloud API calls",        s: "in local mode" },
    ];
    lambdaStats.forEach((stat, i) => {
      const y = 0.88 + i * 1.08;
      s.addShape("rect", {
        x: 6.1, y: y, w: 3.5, h: 0.9,
        fill: { color: i % 2 === 0 ? C.primary : C.secondary }, shadow: makeShadow()
      });
      s.addText(stat.v, {
        x: 6.1, y: y + 0.05, w: 3.5, h: 0.46,
        fontSize: 28, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0
      });
      s.addText(stat.l, {
        x: 6.1, y: y + 0.52, w: 3.5, h: 0.21,
        fontSize: 10, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0
      });
      s.addText(stat.s, {
        x: 6.1, y: y + 0.72, w: 3.5, h: 0.16,
        fontSize: 8, color: C.faint, fontFace: "Calibri",
        align: "center", italic: true, margin: 0
      });
    });

    s.addNotes(`Lambda regression testing uses LocalStack — an open-source AWS cloud emulator that runs in Docker.

The workflow is fully automated:
1. Testcontainers spins up the LocalStack container once per session, so startup cost is paid once regardless of how many test cases you run.
2. The framework packages sample_lambda.py into a ZIP, creates the Lambda function, and sets up all dependent resources (S3 buckets, DynamoDB tables) as declared in the test case's "setup" block.
3. Each test case sends a trigger_event payload (e.g., an S3 PutObject event) and validates the response body, HTTP status code, and any side-effects like DynamoDB writes.
4. At the end of the run, pytest-html produces a visual HTML report and a JSON artefact suitable for CI systems.

Zero real AWS API calls are made in local mode — this means zero cloud costs and consistent, reproducible results in any CI environment.`);
  }

  // ===========================================================================
  // SLIDE 7 – LEX BOT TESTING
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "Lex Bot NLU Testing", "Automated intent recognition, slot filling, and multi-turn conversation validation");
    addFooter(s);

    // Left column – features
    const lexFeatures = [
      { icon: iconRobot,  title: "Single-turn tests",       body: "Validates intent, slot values, and response messages for a single user utterance." },
      { icon: iconLoop,   title: "Multi-turn conversations", body: "Full dialog flows: ElicitSlot → ConfirmIntent → Fulfilled — tested end-to-end in sequence." },
      { icon: iconStream, title: "Fallback intent testing",  body: "Deliberately sends unrecognised input; validates FallbackIntent is triggered correctly." },
      { icon: iconShield, title: "Session attribute injection", body: "Pre-injects session attributes at test start; validates them at every turn of the conversation." },
    ];

    lexFeatures.forEach((feat, i) => {
      const y = 0.88 + i * 1.1;
      const featureColor = [C.primary, C.secondary, C.accent, C.accentAlt][i];
      s.addShape("rect", { x: 0.4, y: y, w: 5.6, h: 0.92, fill: { color: C.white }, shadow: makeShadow() });
      s.addShape("rect", { x: 0.4, y: y, w: 0.18, h: 0.92, fill: { color: featureColor } });
      // Oval circle behind icon for contrast (consistent with slide 6)
      s.addShape("oval", { x: 0.65, y: y + 0.19, w: 0.54, h: 0.54, fill: { color: featureColor } });
      s.addImage({ data: feat.icon, x: 0.70, y: y + 0.24, w: 0.44, h: 0.44 });
      s.addText(feat.title, {
        x: 1.25, y: y + 0.1, w: 4.6, h: 0.3,
        fontSize: 12, bold: true, color: C.darkText, fontFace: "Calibri", margin: 0
      });
      s.addText(feat.body, {
        x: 1.25, y: y + 0.44, w: 4.6, h: 0.44,
        fontSize: 9.5, color: C.midText, fontFace: "Calibri",
        lineSpacingMultiple: 1.25, margin: 0
      });
    });

    // Right – dialog state table
    s.addShape("rect", { x: 6.15, y: 0.83, w: 3.45, h: 0.38, fill: { color: C.primary } });
    s.addText("Dialog States Covered", {
      x: 6.25, y: 0.85, w: 3.25, h: 0.3,
      fontSize: 11, bold: true, color: C.white, fontFace: "Calibri", margin: 0
    });

    const states = [
      ["ElicitSlot",          "Asking for missing slot"],
      ["ConfirmIntent",       "Confirm before fulfilling"],
      ["Fulfilled",           "Intent fulfilled"],
      ["ReadyForFulfillment", "Ready for external call"],
      ["Failed",              "Dialog failed / aborted"],
      ["Close",               "Conversation ended"],
    ];

    s.addShape("rect", { x: 6.15, y: 1.21, w: 3.45, h: states.length * 0.54 + 0.1, fill: { color: C.white }, shadow: makeShadow() });
    states.forEach((row, i) => {
      const y = 1.28 + i * 0.54;
      if (i % 2 === 0) {
        s.addShape("rect", { x: 6.15, y: y, w: 3.45, h: 0.54, fill: { color: C.tealLight } });
      }
      s.addText(row[0], {
        x: 6.25, y: y + 0.1, w: 1.6, h: 0.3,
        fontSize: 9, bold: true, color: C.primary, fontFace: "Consolas", margin: 0
      });
      s.addText(row[1], {
        x: 7.9, y: y + 0.1, w: 1.6, h: 0.3,
        fontSize: 9, color: C.midText, fontFace: "Calibri", margin: 0
      });
    });

    s.addNotes(`Lex V2 bot testing covers the full NLU pipeline.

Key testing capabilities:
- Single-turn: send an utterance, validate the intent name, slot values, and the exact bot response message (case-insensitive substring matching).
- Multi-turn: maintain a session across multiple turns using a unique session ID per test. Validate dialog state at each step.
- Fallback testing: verifies that unrecognised input correctly routes to the FallbackIntent. This is often missed in manual testing.
- Session attributes: inject attributes at the start of a conversation and verify they're preserved and accessible throughout. This is critical for personalization flows.

All 6 dialog states are covered: ElicitSlot, ConfirmIntent, Fulfilled, ReadyForFulfillment, Failed, and Close. This gives complete coverage of the Lex V2 dialog management lifecycle.`);
  }

  // ===========================================================================
  // SLIDE 8 – CI/CD INTEGRATION
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "CI/CD Integration · GitHub Actions", "Automated test execution on every push and pull request to main");
    addFooter(s);

    // Pipeline steps
    const pipeline = [
      { n: "1", icon: iconCI,    color: C.primary,   label: "Trigger",          body: "Push / PR to main\nbranch triggers workflow" },
      { n: "2", icon: iconCogs,  color: C.secondary, label: "Setup",            body: "Python env,\ndependencies, .env secrets" },
      { n: "3", icon: iconPlay,  color: C.accent,    label: "Mock Tests",       body: "MOCK_AWS=true\nAll unit + flow tests" },
      { n: "4", icon: iconBolt,  color: C.accentAlt, label: "Real Tests",       body: "MOCK_AWS=false\nwith OIDC role ARN" },
      { n: "5", icon: iconCheck, color: C.accent,    label: "Report",           body: "HTML + JSON artefacts\nuploaded to Actions" },
    ];

    const bw = 1.55;
    pipeline.forEach((p, i) => {
      const x = 0.35 + i * 1.87;
      s.addShape("rect", { x: x, y: 0.88, w: bw, h: 2.9, fill: { color: C.white }, shadow: makeShadow() });
      s.addShape("rect", { x: x, y: 0.88, w: bw, h: 0.4, fill: { color: p.color } });
      s.addText(p.n, {
        x: x, y: 0.88, w: bw, h: 0.4,
        fontSize: 16, bold: true, color: C.darkBg, align: "center", valign: "middle",
        fontFace: "Calibri", margin: 0
      });
      s.addShape("oval", { x: x + (bw - 0.58) / 2, y: 1.4, w: 0.58, h: 0.58, fill: { color: p.color } });
      s.addImage({ data: p.icon, x: x + (bw - 0.58) / 2 + 0.07, y: 1.47, w: 0.44, h: 0.44 });
      s.addText(p.label, {
        x: x, y: 2.1, w: bw, h: 0.34,
        fontSize: 12, bold: true, color: C.darkText, align: "center", fontFace: "Calibri", margin: 0
      });
      s.addText(p.body, {
        x: x + 0.1, y: 2.5, w: bw - 0.2, h: 0.8,
        fontSize: 9, color: C.midText, align: "center", fontFace: "Calibri",
        lineSpacingMultiple: 1.3, margin: 0
      });
      // Arrow – centred in the 0.32" inter-card gap (card_end+0.07 → card_end+0.25)
      if (i < 4) {
        s.addText("›", {
          x: x + bw + 0.07, y: 1.5, w: 0.18, h: 0.5,
          fontSize: 22, bold: true, color: C.secondary, fontFace: "Calibri",
          align: "center", valign: "middle", margin: 0
        });
      }
    });

    // Required secrets table
    s.addShape("rect", { x: 0.4, y: 4.05, w: 9.2, h: 0.38, fill: { color: C.primary } });
    s.addText("Required GitHub Secrets", {
      x: 0.5, y: 4.07, w: 9, h: 0.3,
      fontSize: 11, bold: true, color: C.white, fontFace: "Calibri", margin: 0
    });

    const secrets = [
      ["AWS_ROLE_ARN", "OIDC trust role ARN for GitHub Actions"],
      ["AWS_REGION", "e.g. us-east-1 or eu-west-2"],
      ["CONNECT_INSTANCE_ID", "Amazon Connect instance ID or ARN"],
    ];
    s.addShape("rect", { x: 0.4, y: 4.43, w: 9.2, h: 0.78, fill: { color: C.white }, shadow: makeShadow() });
    secrets.forEach((row, i) => {
      s.addText(row[0], {
        x: 0.55, y: 4.47 + i * 0.24, w: 2.8, h: 0.22,
        fontSize: 9, bold: true, color: C.primary, fontFace: "Consolas", margin: 0
      });
      s.addText(row[1], {
        x: 3.55, y: 4.47 + i * 0.24, w: 5.8, h: 0.22,
        fontSize: 9, color: C.midText, fontFace: "Calibri", margin: 0
      });
    });

    s.addNotes(`The framework ships with a pre-configured GitHub Actions workflow.

The pipeline runs in two modes:
- Mock mode (MOCK_AWS=true): runs by default, no AWS credentials needed, suitable for every PR and push.
- Real mode (MOCK_AWS=false): requires an OIDC trust relationship between GitHub and the AWS IAM role. This is the recommended approach — no long-lived access keys stored in secrets.

Three GitHub secrets are required: the IAM role ARN, the AWS region, and the Connect instance ID.

The workflow uploads HTML and JSON test reports as artefacts, so you can review results directly in the GitHub Actions UI without needing local access to AWS.

Tip: the .github folder must be at the repository root for Actions to detect the workflow. If this project is a subdirectory of a larger monorepo, move the .github folder accordingly.`);
  }

  // ===========================================================================
  // SLIDE 9 – TECH STACK
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "Technology Stack", "Tools, libraries, and AWS services used across all three testing modules");
    addFooter(s);

    const tools = [
      { name: "Python 3.x",          cat: "Language",       color: C.primary },
      { name: "pytest",               cat: "Test Runner",    color: C.primary },
      { name: "boto3 / botocore",     cat: "AWS SDK",        color: C.secondary },
      { name: "LocalStack",           cat: "Cloud Emulator", color: C.secondary },
      { name: "Testcontainers",       cat: "Docker Mgmt",    color: C.secondary },
      { name: "Amazon Connect",       cat: "AWS Service",    color: C.accent },
      { name: "AWS Chime SDK",        cat: "Call Simulation",color: C.accent },
      { name: "Amazon Lex V2",        cat: "NLU / Chatbot",  color: C.accent },
      { name: "AWS Lambda",           cat: "Serverless",     color: C.primary },
      { name: "Amazon DynamoDB",      cat: "NoSQL DB",       color: C.secondary },
      { name: "Amazon S3",            cat: "Object Storage", color: C.secondary },
      { name: "CloudWatch Logs",      cat: "Observability",  color: C.accent },
      { name: "GitHub Actions",       cat: "CI/CD",          color: C.primary },
      { name: "pytest-html",          cat: "Reporting",      color: C.accentAlt },
      { name: "python-dotenv",        cat: "Config",         color: C.accentAlt },
    ];

    // 3-row grid of badges
    const cols = 5, rows = 3;
    const cardW = 1.72, cardH = 0.88, padX = 0.42, padY = 0.88, gapX = 0.1, gapY = 0.12;
    tools.slice(0, cols * rows).forEach((t, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      const x = padX + col * (cardW + gapX);
      const y = padY + row * (cardH + gapY);
      s.addShape("rect", { x, y, w: cardW, h: cardH, fill: { color: C.white }, shadow: makeShadow() });
      s.addShape("rect", { x, y, w: cardW, h: 0.26, fill: { color: t.color } });
      s.addText(t.cat, {
        x, y: y + 0.03, w: cardW, h: 0.22,
        fontSize: 8, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", margin: 0
      });
      s.addText(t.name, {
        x: x + 0.08, y: y + 0.32, w: cardW - 0.16, h: 0.48,
        fontSize: 10, bold: true, color: C.darkText, fontFace: "Calibri",
        align: "center", valign: "middle", lineSpacingMultiple: 1.2, margin: 0
      });
    });

    // Summary highlights row – fills the dead space below the card grid
    const highlights = [
      { val: "Zero cloud cost",  sub: "local & CI mode" },
      { val: "pytest native",    sub: "familiar toolchain" },
      { val: "Docker emulation", sub: "LocalStack powered" },
      { val: "JSON-driven tests",sub: "no Python required" },
    ];
    highlights.forEach((h, i) => {
      const hx = 0.5 + i * 2.28;
      s.addShape("rect", { x: hx, y: 3.96, w: 2.06, h: 1.10, fill: { color: i % 2 === 0 ? C.primary : C.secondary }, shadow: makeShadow() });
      s.addImage({ data: iconCheck, x: hx + 0.79, y: 4.04, w: 0.34, h: 0.34 });
      s.addText(h.val, { x: hx + 0.08, y: 4.44, w: 1.9, h: 0.28, fontSize: 10, bold: true, color: C.white, fontFace: "Calibri", align: "center", margin: 0 });
      s.addText(h.sub, { x: hx + 0.08, y: 4.74, w: 1.9, h: 0.24, fontSize: 8.5, color: C.faint, fontFace: "Calibri", align: "center", italic: true, margin: 0 });
    });

    s.addNotes(`The tech stack is deliberately lightweight and uses all standard open-source tools.

Core: Python with pytest gives the familiar testing experience. boto3 is the official AWS SDK.

Emulation: LocalStack is the star of the Lambda testing suite — it emulates Lambda, S3, DynamoDB, and other AWS services in Docker. Testcontainers manages the container lifecycle from within pytest.

AWS Services: The three main AWS services under test are Amazon Connect, Chime SDK (for call simulation), and Amazon Lex V2 (for NLU testing). Supporting services include Lambda, DynamoDB, S3, and CloudWatch Logs.

Tooling: python-dotenv handles multi-level environment config (test-suite .env overrides repo root .env). pytest-html produces visual reports. GitHub Actions ties it all together in CI.`);
  }

  // ===========================================================================
  // SLIDE 10 – TEST COVERAGE OVERVIEW
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.lightBg };
    addSlideHeader(s, "Test Coverage Overview", "What is tested and validated across all three modules");
    addFooter(s);

    const columns = [
      {
        title: "Voice Flows",
        color: C.primary,
        items: [
          "Happy path – first call (DTMF 1)",
          "Returning caller skip-greeting",
          "DTMF 2 – Secure input sub-flow",
          "DTMF 3 – Lambda integration flow",
          "DTMF 4 – Note-taking sub-flow",
          "DTMF 5 – Chat transfer",
          "After-hours closed behaviour",
          "Invalid DTMF reprompt",
          "No-input timeout reprompt",
          "Catastrophic error fallback",
          "Callback scheduling",
          "Multi-turn auth (DTMF PIN entry)",
        ]
      },
      {
        title: "Lambda Functions",
        color: C.secondary,
        items: [
          "S3 PutObject event → DynamoDB write",
          "DynamoDB stream event processing",
          "Async invocation side-effects",
          "FunctionError (error-path cases)",
          "Missing env var error handling",
          "Response body validation",
          "HTTP statusCode assertion",
          "S3 object creation validation",
        ]
      },
      {
        title: "Lex Bots",
        color: C.accent,
        items: [
          "Intent recognition accuracy",
          "Slot filling & elicitation",
          "ConfirmIntent dialog state",
          "Multi-turn conversation tracking",
          "Fallback / unrecognised utterance",
          "Session attribute propagation",
          "Active context tracking",
          "Response message verification",
        ]
      },
    ];

    columns.forEach((col, i) => {
      const x = 0.35 + i * 3.22;
      // Header
      s.addShape("rect", { x, y: 0.78, w: 3.05, h: 0.42, fill: { color: col.color } });
      s.addText(col.title, {
        x: x + 0.1, y: 0.78, w: 2.85, h: 0.42,
        fontSize: 13, bold: true, color: C.white, fontFace: "Calibri",
        align: "center", valign: "middle", margin: 0
      });
      // Card body
      s.addShape("rect", {
        x, y: 1.2, w: 3.05, h: 3.94,
        fill: { color: C.white }, shadow: makeShadow()
      });
      // Items
      col.items.forEach((item, j) => {
        s.addImage({ data: iconCheck, x: x + 0.12, y: 1.28 + j * 0.3, w: 0.22, h: 0.22 });
        s.addText(item, {
          x: x + 0.4, y: 1.28 + j * 0.3, w: 2.55, h: 0.26,
          fontSize: 8.5, color: C.midText, fontFace: "Calibri", margin: 0
        });
      });
    });

    s.addNotes(`This slide shows the breadth of test coverage across all three modules.

Voice flows: 12 test cases cover the full IVR tree — happy-path routing, returning-caller detection, every DTMF branch, after-hours handling, error recovery, callback scheduling, and multi-turn DTMF authentication.

Lambda: 8 test cases cover the major Lambda event types (S3, DynamoDB streams), error paths, and side-effect validation. All run against LocalStack with zero cloud cost.

Lex: 8 test categories cover the complete Lex V2 dialog lifecycle — from single intent recognition through complex multi-turn conversations with session context.

The total number of test cases grows as the JSON files grow — teams can add new scenarios without writing any Python code.`);
  }

  // ===========================================================================
  // SLIDE 11 – GETTING STARTED
  // ===========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: C.darkBg };

    // Left accent bar
    s.addShape("rect", { x: 0, y: 0, w: 0.22, h: 5.625, fill: { color: C.accent } });

    // Title area
    s.addShape("rect", { x: 0.22, y: 0, w: 9.78, h: 0.7, fill: { color: C.primary } });
    s.addText("Getting Started", {
      x: 0.55, y: 0.1, w: 9, h: 0.52,
      fontSize: 28, bold: true, color: C.white, fontFace: "Calibri", margin: 0
    });

    // Steps – left
    const gettingStartedSteps = [
      { n: "1", cmd: "git clone https://github.com/alokkulkarni/amazon_connect_testing.git", label: "Clone the repository" },
      { n: "2", cmd: "pip install -r requirements.txt",                                    label: "Install Python dependencies" },
      { n: "3", cmd: "cp .env.example .env  &&  vim .env",                                label: "Configure environment variables" },
      { n: "4", cmd: "MOCK_AWS=true pytest voice_testing/",                               label: "Run voice tests in mock mode" },
      { n: "5", cmd: "./lambda_testing/run_lambda_tests.sh",                              label: "Run Lambda tests with LocalStack" },
      { n: "6", cmd: "pytest lex_testing/ -v --html=report.html",                        label: "Run Lex bot tests" },
    ];

    // Tighter pitch (0.67") ensures last code block stays 0.35" above footer
    gettingStartedSteps.forEach((step, i) => {
      const y = 0.90 + i * 0.67;
      // Step circle
      s.addShape("oval", { x: 0.35, y: y + 0.05, w: 0.44, h: 0.44, fill: { color: C.accent } });
      s.addText(step.n, {
        x: 0.35, y: y + 0.05, w: 0.44, h: 0.44,
        fontSize: 13, bold: true, color: C.darkBg, align: "center", valign: "middle",
        fontFace: "Calibri", margin: 0
      });
      s.addText(step.label, {
        x: 0.9, y: y + 0.04, w: 4.1, h: 0.24,
        fontSize: 9.5, color: C.faint, fontFace: "Calibri", italic: true, margin: 0
      });
      // Code block
      s.addShape("rect", { x: 0.9, y: y + 0.28, w: 8.7, h: 0.32, fill: { color: "0D2137" } });
      s.addText(step.cmd, {
        x: 0.98, y: y + 0.3, w: 8.5, h: 0.26,
        fontSize: 9, color: C.accent, fontFace: "Consolas", margin: 0
      });
    });

    // Footer
    s.addShape("rect", { x: 0.22, y: 5.2, w: 9.78, h: 0.425, fill: { color: C.secondary } });
    s.addText("github.com/alokkulkarni/amazon_connect_testing  ·  MIT License  ·  Contributions welcome", {
      x: 0.4, y: 5.22, w: 9.4, h: 0.36,
      fontSize: 9, color: C.faint, fontFace: "Calibri", align: "center", margin: 0
    });

    s.addNotes(`Getting started is straightforward — the entire framework is runnable in under 10 minutes.

Steps 1-3: Clone, install deps, configure .env. The key variables are MOCK_AWS (set to true for local development), CONNECT_INSTANCE_ID, CHIME_PHONE_NUMBER, and CHIME_SMA_ID.

Step 4: Run voice tests in mock mode first. This validates the test runner, JSON parsing, and fixture setup without needing real AWS.

Step 5: Lambda tests require Docker (for LocalStack). Run the shell script — it handles container lifecycle automatically.

Step 6: Lex tests require real AWS credentials pointing at a Lex V2 bot. Set LEX_BOT_ID, LEX_BOT_ALIAS_ID, and LEX_LOCALE_ID in your .env.

For CI, push to GitHub and add the three required secrets. The Actions workflow runs mock tests immediately and real tests when credentials are configured.`);
  }

  // ===========================================================================
  // WRITE FILE
  // ===========================================================================
  const outPath = "amazon_connect_testing_overview.pptx";
  await pres.writeFile({ fileName: outPath });
  console.log(`✅  Presentation written: ${outPath}`);
}

buildPresentation().catch(err => {
  console.error("❌  Error building presentation:", err);
  process.exit(1);
});
