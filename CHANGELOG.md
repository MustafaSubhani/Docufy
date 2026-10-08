# Changelog

## 0.4.0

Docufy writes new documents. A short brief such as "write a client proposal for ACME Fleet" is outlined by the planner, then one writer agent drafts each section, in parallel on Claude or OpenAI and one at a time on the local model. Details the brief does not give are marked to be confirmed, and the company pages still come from the brand pack. Without a model the reply offers one click to write it with an available model. Every job shows its steps as they happen: the outline, each section agent, layout, the page checks, and the preview, with timings. The steps fold into one line when the job is done. Ctrl K finds any chat or page, the sidebar can be hidden, the preview can be resized, replies can be copied, and the chat follows new output only while you are at the bottom.

## 0.3.0

The studio is now Docufy. Pick the model for each message in the composer: Format only, Local, Claude, or OpenAI. With a document open, a message changes it and saves a new version: replace words, set the title or client, remove a section, switch the company pages, or rewrite a section with a model. Each job shows its step and can be stopped or tried again. Replies remember the chat and stream from the local model. Word, PDF, slides, CSV, and spreadsheet files are read as notes. Downloads are named after the document. Cloud calls now record their cost, so the monthly budget can stop them. A reply cut off at the output limit keeps its first part. Chats can be renamed, searched by message, exported, and imported. Settings shows backups and a setup check.

## 0.2.0

Local production pass. One install script and a first-run check. SQLite stays in WAL mode and migrates on startup, with a copy taken first when the database already has rows. A crashed run stays on its last checkpoint and is not sent to the model again. The workspace can be backed up, restored, exported, and imported. Uploads are checked against their file type. An optional app password is stored as a hash. CI runs the test suite.
