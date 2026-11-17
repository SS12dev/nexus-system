---
name: frontend-design
description: Build interfaces that look designed and work for everyone - a clear visual direction, type and spacing scales, colour with contrast, responsive layout, states for loading, empty and error, keyboard and screen-reader access, and motion with restraint. Use when building or reviewing any web UI, page, game screen or component.
metadata:
  category: engineering
---

# Frontend design

The difference between a demo and a product is mostly here. Decide the look before writing
components, then be consistent.

## 1. Pick a direction first

Write three lines before any CSS: the feeling (calm and editorial, playful, dense and
technical), the one thing a visitor should notice first, and what the app is not. Then
choose, and record as CSS variables:

- **Type:** one or two families. A scale of five or six sizes (for example 12, 14, 16, 20,
  28, 40 px), line height 1.5 for text and about 1.2 for headings, line length 45 to 75
  characters. System fonts are fine; if a web font is used, self-host it.
- **Colour:** one neutral ramp, one accent, plus success, warning and danger. Text contrast
  at least 4.5:1 (3:1 for large text and for the edges of controls). Never rely on colour
  alone to carry meaning.
- **Space:** a scale in steps of 4 (4, 8, 12, 16, 24, 32, 48, 64). Use it for every margin,
  padding and gap. Unequal, arbitrary spacing is what makes a page look amateur.
- **Shape and depth:** one radius scale, one or two shadow levels, one border colour.

Avoid the generic look: centred everything, a purple gradient, a card inside a card, emoji
as icons, five font sizes on one screen. Choose something specific to this product.

## 2. Layout

- Design for a 360 px wide phone first, then widen. Test 360, 768 and 1280.
- Use grid and flexbox with `gap`; no fixed pixel widths for content, no horizontal scroll.
- Establish hierarchy with size, weight and space before reaching for colour or boxes.
- Align to a small number of edges. Group related things; separate unrelated ones with space.
- Touch targets at least 44 by 44 px, with space between them.
- Respect safe areas on phones and `prefers-color-scheme` when both themes are offered.

## 3. Every state, not only the happy one

For each screen and control, build and look at:

- **Empty:** first run, with a line on what goes here and the action that fills it.
- **Loading:** a skeleton or spinner after about 300 ms; no layout jump when data arrives.
- **Error:** what went wrong in plain words and what to do; the user's input is kept.
- **Success:** confirmation that the action worked.
- **Disabled, hover, focus, active, selected** for interactive elements.
- **Too much:** long names, hundreds of items, a huge number.

## 4. Accessibility

- Real elements: `button` for actions, `a` for navigation, `label` tied to every input,
  headings in order, lists as lists, one `main`.
- Everything works by keyboard, in a sensible order, with a visible focus ring. Dialogs trap
  focus and close on Escape; focus returns to what opened them.
- Images have `alt`; decorative ones have empty `alt`. Icon-only buttons have an accessible
  name.
- Status changes are announced (`aria-live`) where a sighted user would see them.
- Honour `prefers-reduced-motion`. No content that flashes.
- Form errors are next to the field, in text, and linked to it.

## 5. Motion and feedback

- Every action gets a response within 100 ms, even if only a pressed state.
- Transitions of 150 to 250 ms with an ease-out curve, on `transform` and `opacity`.
- Motion explains a change (where something came from or went); it never delays the user.

## 6. Performance that people feel

- Ship little JavaScript; split out heavy, rarely used parts.
- Size images to their display size, use modern formats, set width and height, lazy-load
  what is off screen.
- Avoid layout shift: reserve space for anything that loads late.
- Lists of more than a few hundred rows are windowed.

## 7. Words

Buttons say what they do ("Save note", not "Submit"). Messages are short, specific and
blame nothing on the user. One name per thing throughout. No developer or debug text on the
page, ever.

## Check before reporting

Open the built app at phone and laptop widths and look at it: does it match the direction
you wrote down, is anything cut off, overlapping or misaligned, do all states exist, can you
do the main journey with only the keyboard, are there console errors?
