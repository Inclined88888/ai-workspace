"""The assistant: one agent loop shared by every channel (web chat, terminal, Telegram, schedules, company agents).

Each turn sees: who you are (profile memories), memories relevant to the message, an index of learned skills,
and the workspace tools. After a turn, learn.py mines the conversation for new memories and reusable skills.
"""