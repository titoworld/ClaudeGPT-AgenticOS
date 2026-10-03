// The tests check the interface's texts in Catalan, the language they were written in: it
// is the browser's language in every test (i18n.test.ts checks the others).
Object.defineProperty(navigator, 'languages', { configurable: true, get: () => ['ca'] });
Object.defineProperty(navigator, 'language', { configurable: true, get: () => 'ca' });
