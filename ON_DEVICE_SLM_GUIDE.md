# On-Device SLMs: Running AI Directly on iOS & Android

This document outlines which Small Language Models (SLMs) can run directly on mobile devices and provides a step-by-step guide to implementing them using **MediaPipe**, the most accessible cross-platform framework for this task.

## 1. Why Run On-Device?
*   **Privacy**: Data (e.g., health notes, personal chats) never leaves the phone.
*   **Latency**: Instant response, no network round-trip delay.
*   **Offline Capability**: Works in airplane mode or poor signal areas.
*   **Cost**: Zero cloud inference costs.

## 2. Top SLM Candidates for Mobile (2026 Context)
Mobile devices have limited RAM and thermal constraints. You typically use **Quantized** versions (compressed from 16-bit to 4-bit) of these models:

| Model | Size (Params) | RAM Required (4-bit) | Best For |
| :--- | :--- | :--- | :--- |
| **Google Gemma 2B** | 2 Billion | ~1.5 GB | General chat, simple instructions. Very fast. |
| **Microsoft Phi-3 Mini** | 3.8 Billion | ~2.5 GB | Reasoning, coding, math. Excellent balance. |
| **Meta Llama 3 (8B)** | 8 Billion | ~5.5 GB | Complex nuances, summarization. Needs high-end phones (Pixel 8 Pro, iPhone 15 Pro). |
| **TinyLlama 1.1B** | 1.1 Billion | ~800 MB | Extremely constrained devices, basic autocompletion. |

---

## 3. How to Implement: The Frameworks

### Option A: MediaPipe LLM Inference (Recommended)
*   **Provider**: Google.
*   **Pros**: Cross-platform (Android, iOS, Web), easy API, official support for Gemma/Phi/Llama.
*   **Cons**: Less granular control than raw PyTorch.

### Option B: MLC LLM
*   **Provider**: Open Source (TVM based).
*   **Pros**: High performance, uses WebGPU/Metal directly.
*   **Cons**: More complex build setup.

### Option C: ExecuTorch
*   **Provider**: Meta (PyTorch).
*   **Pros**: Native PyTorch workflow.
*   **Cons**: Still evolving, best for Llama specific workflows.

---

## 4. Step-by-Step Implementation Guide (using MediaPipe)

We will use **Google MediaPipe** because it abstracts the complexity of GPU acceleration (Metal on iOS, OpenCL/Vulkan on Android).

### Phase 1: Model Preparation (The Desktop Step)
You cannot push raw `.safetensors` or Hugging Face weights to a phone. You must convert them.

1.  **Install Converter**:
    ```bash
    pip install mediapipe
    ```
2.  **Download Weights**: Get the weights for your model (e.g., `Gemma 2b-it-cpu`) from Kaggle or Hugging Face.
3.  **Convert & Quantize**:
    Use the MediaPipe conversion script to create a mobile-optimized `.bin` file.
    ```bash
    python -m mediapipe.tasks.python.genai.converter \
      --conversion_type INT4 \
      --input_checkpoint ./gemma-2b-it-gpu-int4.ckpt \
      --model_type GEMMA_2B \
      --backend GPU \
      --output_model gemma_mobile.bin
    ```
    *Result*: You now have `gemma_mobile.bin` (~1.5GB).

### Phase 2: Android Implementation

1.  **Add Dependencies** (`build.gradle`):
    ```groovy
    implementation 'com.google.mediapipe:tasks-genai:0.10.14'
    ```

2.  **Push Model to Device**:
    Copy `gemma_mobile.bin` to `/data/local/tmp/` via ADB or include it as an Asset (warning: assets have size limits, better to download on first launch).

3.  **Initialize Engine**:
    ```kotlin
    import com.google.mediapipe.tasks.genai.llminference.LlmInference

    // Configure
    val options = LlmInference.LlmInferenceOptions.builder()
        .setModelPath("/path/to/gemma_mobile.bin")
        .setMaxTokens(512)
        .setResultListener { partialResult, done -> 
            // Handle streaming tokens here for "typing" effect
            print(partialResult) 
        }
        .build()

    // Create Instance
    val llmInference = LlmInference.createFromOptions(context, options)
    ```

4.  **Run Inference**:
    ```kotlin
    val prompt = "Summarize this email: ..."
    llmInference.generateResponseAsync(prompt)
    ```

### Phase 3: iOS Implementation

1.  **Add Pod** (`Podfile`):
    ```ruby
    pod 'MediaPipeTasksGenAi'
    ```

2.  **Add Model**: Drag `gemma_mobile.bin` into your Xcode project (checking "Add to targets").

3.  **Initialize Engine (Swift)**:
    ```swift
    import MediaPipeTasksGenAi

    let modelPath = Bundle.main.path(forResource: "gemma_mobile", ofType: "bin")!
    let options = LlmInferenceOptions()
    options.modelPath = modelPath
    options.maxTokens = 512

    let llm = try LlmInference(options: options)
    ```

4.  **Run Inference**:
    ```swift
    let prompt = "Explain quantum physics briefly."
    
    // Async generation
    try llm.generateResponseAsync(inputText: prompt) { partialResult, error in
        if let text = partialResult {
            print(text) // Update UI
        }
    }
    ```

## 5. Best Practices for Mobile AI
1.  **Download, Don't Bundle**: These models are 1GB+. Do not bundle them in your initial App Store IPA/APK. Download them on first launch or "On Demand" when the user wants to use the AI feature.
2.  **Warm-up**: The first inference takes longer as the model loads into RAM. Show a "Loading AI..." spinner.
3.  **Battery**: Heavy inference drains battery. Use it for short tasks (summarization, reply suggestion), not long-running agents.
4.  **Prompt Engineering**: SLMs are less smart than GPT-4. Be very specific in your prompts. Use "Few-Shot" prompting (give examples) to improve quality.
