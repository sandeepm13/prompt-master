# Privacy Policy for dotix

**Last updated:** November 20, 2026

**dotix** ("we", "our", or "the app") is committed to protecting your privacy. This Privacy Policy explains how information is handled when you use the dotix desktop application and website.

---

### 1. Information Processing & Data Minimization
* **Prompt Text**: When you request a prompt rewrite (via the floating dot or shortcut `Ctrl+Alt+P`), the text in your focused input box is processed temporarily to generate an improved prompt.
* **Clipboard Handling**: The app uses the system clipboard for a brief moment solely to read and replace the prompt text. Your previous clipboard content is restored immediately afterwards.
* **Local Context**: If context gathering is enabled, minimal local context (such as the active application name or active browser tab domain) is used strictly to enhance the accuracy of the rewrite.
* **No Intermediary Storage**: We operate no intermediary cloud servers that collect, store, or log your prompts, text snippets, or personal data.

---

### 2. Third-Party AI Providers
* dotix connects directly from your device to the AI service provider you configure (such as Groq, Google Gemini, OpenAI, OpenRouter, or a local Ollama instance).
* When a prompt is rewritten, your text is transmitted directly to that provider's official API using your personal API key.
* The processing of your prompts by AI providers is governed by the respective provider's terms and privacy policies.

---

### 3. Local Storage of Credentials & Settings
* Your API keys, custom prompt styles, and settings are stored locally on your device in `%APPDATA%\MasterPrompt\config.toml`.
* They are never transmitted to us or any third party other than the designated AI API endpoints during rewrite requests.

---

### 4. Telemetry & Analytics
* The dotix desktop application contains **no telemetry, analytics trackers, or user profiling tools**.
* We do not collect personal identifiers, email addresses, or system telemetry.

---

### 5. Changes to This Policy
We may update this Privacy Policy from time to time. Any updates will be posted in this repository.

---

### 6. Contact
If you have questions about this Privacy Policy or dotix, please open an issue on GitHub:
https://github.com/sandeepm13/prompt-master
