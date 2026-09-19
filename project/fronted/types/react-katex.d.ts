declare module 'react-katex' {
  import { ComponentType, ReactNode } from 'react';
  interface MathComponentProps {
    math: string;
    children?: ReactNode;
    renderError?: (error: unknown) => ReactNode;
    errorColor?: string;
    [key: string]: unknown;
  }
  export const InlineMath: ComponentType<MathComponentProps>;
  export const BlockMath: ComponentType<MathComponentProps>;
}
