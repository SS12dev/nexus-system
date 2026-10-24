---
name: budgeting
description: Help with personal budgets, spending reviews from bank exports, savings goals, bills and subscriptions - analysis and plain explanations of financial ideas, never investment, tax or legal advice. Use for budgeting, spending, saving and money questions.
metadata:
  category: life
---

# Budgeting

This is information and organisation, not financial advice. For investments, tax, debt
solutions or legal questions, explain the general idea and suggest a qualified adviser.

## A budget

Income after tax; fixed costs (rent, bills, loans, subscriptions); variable (food,
transport, fun); savings. A simple starting split is 50/30/20 (needs/wants/savings); adjust
to their reality. Show it as a table with monthly amounts and percentages.

## Spending review from a bank export

Use the `data-analysis` skill with `run_code` on the CSV or Excel the user uploaded:

1. Clean dates and amounts; separate income from spending; drop transfers between their own
   accounts.
2. Categorise by merchant (groceries, eating out, transport, subscriptions, shopping, bills).
3. Monthly totals by category, top merchants, and recurring charges (same merchant and similar
   amount each month): these are the subscriptions.
4. Compare with their budget or last month; point out the three biggest changes.
5. A chart with the `charts` skill when helpful.

Never ask for or store bank passwords or full card numbers.

## Goals

For a savings goal: amount, date, monthly amount needed, and what to trim to get there, from
their actual spending.

## Explaining ideas

Compound interest, index funds, emergency funds, credit scores: plainly, with a worked example
in their currency, and the risks.
