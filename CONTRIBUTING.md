# Contributing to OpenGIKAI

[🇯🇵 日本語版はこちら / Japanese](./CONTRIBUTING.ja.md)

Thank you for your interest in contributing to OpenGIKAI! This project aims to make Japanese parliamentary proceedings accessible to everyone while maintaining political neutrality and transparency.

## Code of Conduct

- Be respectful and constructive
- Maintain political neutrality — OpenGIKAI is a transparency tool, not an advocacy platform
- Focus on accuracy and accessibility

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/wharfe/open-gikai/issues) to report bugs or suggest features
- Include steps to reproduce for bugs
- For feature requests, explain the use case

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Make your changes
4. Run linting and tests (`npm run lint`)
5. Commit with a clear message
6. Push and open a Pull Request

### Development Setup

```bash
git clone https://github.com/wharfe/open-gikai.git
cd open-gikai
npm install
npm run dev
```

## Guidelines

### Code

- TypeScript strict mode
- Code comments in English
- User-facing text in Japanese
- Use Tailwind CSS for styling
- Follow existing patterns in the codebase

### AI Prompts

Changes to AI prompts (summarization, classification, etc.) require extra scrutiny:

- Explain the reasoning for the change
- Show before/after examples of generated output
- Ensure political neutrality is maintained
- Changes to prompts should be reviewed by at least one other contributor

### Commits

- Use clear, descriptive commit messages
- Reference issue numbers where applicable

## Areas Where Help Is Needed

- Frontend components and UI improvements
- Accessibility (a11y) enhancements
- Performance optimization
- Documentation and translations
- AI prompt refinement (with neutrality safeguards)
- Testing

## Questions?

Open a [GitHub Discussion](https://github.com/wharfe/open-gikai/discussions) or an issue.
