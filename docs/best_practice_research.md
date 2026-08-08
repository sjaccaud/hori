# Overview
5 AI coding /skills (from Matt Pocock's GitHub repository) discussed in the video:
 * Grill Me: Forces the AI to act as an adversary and relentlessly interview you about your plan until you both reach a shared "design concept," preventing it from rushing into writing bad code.
 * Ubiquitous Language: Directs the AI to scan your codebase and build a markdown file of shared vocabulary. This reduces AI verbosity and ensures it uses your exact domain terminology.
 * TDD (Test-Driven Development): Prevents the AI from "outrunning its headlights" by forcing it to take small steps: write a test first, make it pass, and then refactor.
 * Improve Codebase Architecture: A refactoring skill that tasks the AI with finding related code and wrapping it into testable "deep modules" (large blocks of functionality hidden behind simple interfaces) rather than a mess of shallow, fragmented files.
 * Write a PRD (Product Requirements Document): A planning skill used to explicitly define system design, module changes, and interface boundaries so you can maintain high-level strategic control while delegating the tactical coding to the AI.

# Core Theme
The core theme is that software fundamentals matter now more than they ever have. He heavily critiques the "specs-to-code" movement (the idea that code is cheap and you can just have AI regenerate an entire app from a prompt if something breaks) because it leads to software entropy and unmaintainable architecture.
To counter this, he recommends pulling best practices from classic software engineering books to manage AI workflows. Here are the best practices he describes:
1. Establish a Shared "Design Concept" Before Coding
 * The Problem: The AI builds something completely different than what you had in your head.
 * The Practice: Drawing from Frederick P. Brooks' The Design of Design, Pocock emphasizes that you and the AI must share an invisible, theoretical understanding of what you are building (the "design concept"). Instead of letting the AI eagerly jump into writing code, you should force it to interview you and walk down every branch of the design tree to resolve dependencies first.
2. Define a "Ubiquitous Language"
 * The Problem: The AI is too verbose, and it feels like you are talking past each other.
 * The Practice: Borrowing from Domain-Driven Design (DDD), you need a shared vocabulary. You should maintain a central document (like a markdown file) of specific terminology related to your project. By forcing the AI to strictly adhere to this vocabulary, it communicates more efficiently and writes code that actually aligns with your domain model.
3. Use Strict Feedback Loops & TDD (Test-Driven Development)
 * The Problem: The AI writes code that simply doesn't work because it tries to do too much at once—a concept The Pragmatic Programmer calls "outrunning your headlights."
 * The Practice: You must use strong static typing (like TypeScript) and give the LLM access to automated tests and the browser. Furthermore, enforce TDD. By writing a test first, you force the AI to take small, deliberate steps (pass the test, then refactor) rather than vomiting thousands of lines of unverified code.
4. Architect with "Deep Modules" (Not Shallow Ones)
 * The Problem: AI is notorious for generating codebases full of "shallow modules"—hundreds of tiny, fragmented files with complex dependencies that neither you nor the AI can easily navigate later.
 * The Practice: Referencing John Ousterhout’s A Philosophy of Software Design, Pocock says you should structure your codebase using Deep Modules. These are large blocks of related code that hide massive amounts of internal functionality behind very simple, testable interfaces. This makes the codebase vastly easier for the AI to explore and understand.
5. Design the Interface, Delegate the Implementation
 * The Problem: Shipping code with AI is incredibly fast, but reading and reviewing all of it leads to massive cognitive fatigue for the developer.
 * The Practice: Treat your deep modules as "gray boxes." As the human developer, your job is to strictly design and test the interface (the boundary of the module). Once the boundary and tests are secure, you can completely delegate the messy internal implementation to the AI without having to meticulously review every line it writes.
6. Invest in System Design Every Day
 * The Practice: Quoting Kent Beck, Pocock stresses that you cannot divest from your system's architecture. Treat the AI as your tactical, "on-the-ground" programmer. You must act as the high-level strategist above it, constantly maintaining awareness of how the modules map together and ensuring the long-term health of the codebase. Bad code is more expensive than ever because it prevents AI from helping you scale.
