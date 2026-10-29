import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Moving to another page (a new key) clears the error and tries again. */
  resetKey: string;
}

interface State {
  error: Error | null;
}

/** A page that fails to draw says why, in place of a blank screen. */
export class ErrorBoundary extends Component<Props, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override componentDidUpdate(previous: Props) {
    if (previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  override render() {
    const { error } = this.state;
    if (error === null) {
      return this.props.children;
    }
    return (
      <main className="page" data-testid="page-error">
        <h1>This page could not be shown</h1>
        <p className="status failed" role="alert">
          {error.message}
        </p>
        <p>
          <a href="#/book">Open the book's alerts</a>
        </p>
      </main>
    );
  }
}
