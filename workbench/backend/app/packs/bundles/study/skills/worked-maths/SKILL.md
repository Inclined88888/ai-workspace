---
name: study-worked-maths
description: Solve maths step by step with proper notation, and check each step with a real calculation.
---
# Worked maths

Use this for algebra, calculus, equations, simplifying, factorising, limits and similar problems.

1. Call study_maths with the expression and the task (solve, simplify, factor, expand, differentiate, integrate, limit).
   It returns each step in LaTeX and a checked final answer.
2. Show the steps with LaTeX between dollar signs, one idea per step, in plain words a student can follow.
3. When a student shares their own working, call study_check_steps with their lines; point out the first step that does
   not follow and explain why.
4. Say clearly when a result could not be checked.
