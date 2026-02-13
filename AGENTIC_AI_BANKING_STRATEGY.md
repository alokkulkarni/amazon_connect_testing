# Agentic AI Strategy for Unified Conversational Banking
**Date:** 2026-02-13
**Version:** 1.0
**Context:** Amazon Connect (Voice & Chat) Integration

---

## 1. Executive Summary
The shift from "Static Chatbots" to "Agentic AI" represents a fundamental change in Conversational Banking. Instead of following rigid decision trees, **AI Agents** reason, plan, and execute tasks autonomously using tools.

This strategy defines an **Orchestrator Pattern** where a central "Brain" directs specialized Agents (e.g., Payments Agent, Mortgage Agent) to resolve customer needs across Voice and Chat channels provided by Amazon Connect, ensuring strict adherence to banking security and ethics standards.

---

## 2. Architecture: The Orchestrator Pattern

In a Unified Conversational Banking environment, the "Orchestrator" acts as the Traffic Controller. It does not know how to *do* everything, but it knows *who* does.

### The Flow
1.  **Channel Entry**: Customer speaks (Voice) or types (Chat) into Amazon Connect.
2.  **Transcription/Ingestion**: Audio is converted to text (Amazon Transcribe).
3.  **The Orchestrator (The Router)**:
    *   Analyzes intent and context.
    *   Decomposes the request (e.g., "Transfer $500 to Mom and tell me my balance").
    *   Delegates tasks to Sub-Agents.
4.  **Specialized Agents**:
    *   **Transactional Agent**: Executes payments.
    *   **Information Agent**: Queries Knowledge Base (RAG).
    *   **Advisory Agent**: Analyzes spending patterns.
5.  **Response Synthesis**: The Orchestrator combines outputs into a coherent natural language response sent back to Connect.

---

## 3. Model Strategy: The "Right Model for the Right Journey"

Banking journeys vary in complexity and risk. A "One Size Fits All" model strategy is inefficient and risky. We propose a **Tiered Model Architecture**.

| Tier | Model Type | Journey Examples | Recommended Models | Why? |
| :--- | :--- | :--- | :--- | :--- |
| **Tier 1: High Reasoning** | **Frontier LLMs** | Financial Advice, Complex Disputes, Mortgage Planning | **Claude 3.5 Sonnet, GPT-4o** | Requires high IQ, complex context window, and reasoning capabilities. |
| **Tier 2: Task Execution** | **Mid-Size LLMs** | Payment Transfers, Card Blocking, Account Updates | **Llama 3 (70B), Haiku** | Fast, reliable instruction following for tool use. Lower latency than Frontier models. |
| **Tier 3: Classification** | **SLMs** | Intent Routing, Sentiment Analysis, PII Redaction | **Titan Lite, Phi-3, Gemma** | Extremely fast (<200ms), cheap, runs on minimal hardware. |
| **Tier 4: Sensitive Data** | **Private/Fine-Tuned** | Fraud Detection, Internal Risk Scoring | **Fine-tuned Llama 3 (On-Prem/VPC)** | Data sovereignty requires models that never leave the secure VPC perimeter. |

---

## 4. Governance: The Trust Layer
Agentic AI introduces non-deterministic behavior. In banking, "hallucination" is unacceptable.

### A. Guardrails (The "Safety Net")
Every model invocation must pass through a Guardrail proxy (e.g., Bedrock Guardrails or NeMo).
1.  **Input Rails**: Block malicious prompts ("Jailbreak attempt", "Ignore previous instructions").
2.  **Output Rails**:
    *   **PII Filtering**: Redact SSN, Credit Card numbers if not required.
    *   **Topic Deny-list**: Block political advice, medical advice, or competitor mentions.
    *   **Financial Advice Disclaimer**: Enforce standard disclaimers on advisory outputs.

### B. Security & Authentication
*   **Identity Propagation**: The Agent must perform actions *as the user*. The IAM role assumed by the Agent must map to the Authenticated User's context (OBO - On-Behalf-Of flow).
*   **Tool Authorization**: Before an Agent executes a tool (e.g., `POST /transfer`), a deterministic code layer must validate limits (e.g., "Max $2000 daily") independent of the LLM.

### C. Auditability (Chain of Thought)
*   **Traceability**: Every action must be logged. Not just "User asked X, Bot said Y", but the **Chain of Thought (CoT)**:
    *   *Thought*: "User wants to transfer money."
    *   *Plan*: "Call GetBalance -> If sufficient -> Call PostTransfer."
    *   *Observation*: "Balance is $50."
    *   *Action*: "Inform user balance is insufficient."
*   This trace must be stored in WORM (Write Once Read Many) storage for regulatory audit (Compliance).

---

## 5. Platform Decision: Native AWS vs. Cloud Agnostic

### Approach A: Native AWS (Amazon Bedrock Agents)
*Deep integration with the AWS Ecosystem.*

**Pros:**
*   **Security**: Inherits AWS IAM security, PrivateLink, and VPC controls out of the box. Critical for banking.
*   **Integration**: Seamless connection to Amazon Connect and Lambda tools.
*   **Managed Infrastructure**: No servers to manage. AWS handles memory/state.
*   **Guardrails**: Native integration with Bedrock Guardrails.

**Cons:**
*   **Vendor Lock-in**: Hard to migrate to Azure/GCP later.
*   **Opacity**: "Black box" orchestration. Harder to debug *exactly* why the planner chose step A over B compared to open code.

### Approach B: Cloud Agnostic Framework (LangChain / LangGraph)
*Running open-source frameworks on Containers (ECS/EKS) or Lambda.*

**Pros:**
*   **Flexibility**: Swap models easily (e.g., call Azure OpenAI or Google Gemini if AWS is down).
*   **Control**: Full visibility into the prompt engineering and orchestration logic (Python/TypeScript code).
*   **Portability**: Can run on-premise if regulations change.

**Cons:**
*   **Operational Overhead**: You build it, you run it. You manage state (Redis/DynamoDB), memory, and scaling.
*   **Security Complexity**: You must manually implement PII redaction and secure connectivity.

### Recommendation
**Go Native (AWS Bedrock Agents)** for the core banking bot.
*   **Reasoning**: Financial services require the highest security posture. The operational risk of managing a self-hosted orchestration layer (patching, scaling, securing) outweighs the benefits of portability. AWS Bedrock Agents provide the necessary audit trails and IAM security boundaries required by CIS/SOC2 controls naturally.

---

## 6. Implementation Checklist for Agents

1.  **Define Agent Persona**: "You are a helpful Banking Assistant. You are concise and professional."
2.  **Define Action Groups (Tools)**:
    *   Create OpenAPI schemas (Swagger) for your Core Banking APIs.
    *   *Crucial*: Add detailed descriptions to API fields so the LLM knows *when* and *how* to use them.
3.  **Configure Memory**:
    *   Enable Session Memory to handle context ("Transfer it to *him*" -> knows "him" is the previous payee).
    *   Set TTL (Time To Live) to clear context after the session ends for security.
4.  **Human-in-the-Loop (HITL)**:
    *   Configure specific "Confidence Thresholds". If the Agent is <80% sure of a plan, it must handover to a Human Agent in Amazon Connect.
