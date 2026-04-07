declare module 'vue-markdown-shiki' {
  import type { App, DefineComponent, Plugin } from 'vue';

  export const VueMarkdownIt: DefineComponent<{ content: string }>;
  export const VueMarkDownHeader: DefineComponent<Record<string, never>>;
  export const VueMarkdownItProvider: DefineComponent<Record<string, never>>;

  const plugin: Plugin & {
    install: (app: App) => void;
  };

  export default plugin;
}

declare module 'vue-markdown-shiki/style';
