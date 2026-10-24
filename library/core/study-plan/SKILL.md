---
name: study-plan
description: Build study plans, revision timetables, flashcards and practice quizzes for exams or a subject, using spaced repetition and active recall, fitted around the student's calendar and deadlines. Use when someone is studying, revising or learning a subject.
metadata:
  category: work
---

# Study plans and revision

## Gather

Subjects and topics, exam or deadline dates, how much time per day they have, what they find
hardest, and how they like to study. Check `calendar_list_events` for exams, classes and busy
days when Calendar is connected.

## The plan

- Work back from each deadline. Give harder and heavier topics more sessions and put them
  earlier.
- Sessions of 25-50 minutes, each with one topic and one active task (practice questions,
  explain-it-back, flashcards), never "re-read notes".
- Spaced repetition: review each topic about 1, 3, 7 and 14 days after first studying it.
- Interleave subjects across a day; keep one lighter day a week and a buffer before exams.
- A table by date: day, session, topic, task, done.

With Tasks connected, offer to add the sessions with `tasks_add` (ask before adding many).

## Flashcards and quizzes

- One fact or idea per card; question on the front, short answer on the back.
- Prefer "why" and "how" cards to definitions; add a worked example for methods.
- Output as a CSV (`front,back`) with `save_file` for import into Anki or Quizlet when asked.
- Quizzes: 5-10 questions, mixed difficulty, answers at the end with a one-line explanation.

## Explaining a topic

Start from what they know, one idea at a time, a concrete example, then check with a question
before moving on.
