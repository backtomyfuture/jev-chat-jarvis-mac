# Domain Glossary: jev-chat-jarvis

This glossary defines the ubiquitous language for the codebase. All modules, interfaces, and issue tickets must conform to these terms.

## Core Concepts

### Chat Intake
The deep module responsible for discovering active conversations and acquiring conversation snapshots. It encapsulates reading and parsing chat history behind a clean seam, shielding the rest of the application from how messages are captured.

### ChatSource (Seam)
The seam at which chat acquisition happens.
- **WeChat CLI Adapter**: The primary adapter satisfying `ChatSource`. Interacts directly with the local `wechat-cli` command to extract structured sessions and message history without OCR or screen captures.
- **Chat Snapshot**: The immutable data contract produced by `ChatSource`, containing the chat identifier, recipient name, is_group flag, and an ordered list of recent structured messages with explicit sender roles.

### Judgment
The decision module evaluating a target message in context. Backed by **TypeSafe Jev** (remote API), it returns appeal, urgency, and a reply strategy, plus calibrated confidence. Personal chat assistance only — it does not classify safety, compliance, money, or harm.

### Response Formulation
The module responsible for generating tone-specific candidate replies (via OpenAI or Anthropic compatible remote LLMs) and ranking candidates against the detected intent.

### Fill
The module responsible for safely inserting a candidate reply into WeChat's active input area, prioritizing macOS Accessibility (AX) interfaces.
