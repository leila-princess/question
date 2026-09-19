declare module 'docx-preview' {
  export function renderAsync(data: Blob | ArrayBuffer, container: HTMLElement, styleContainer?: HTMLElement, options?: any): Promise<void>;
}
