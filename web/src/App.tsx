import { type FormEvent, useEffect, useState } from "react";
import { BookPage } from "./BookPage";
import { LoanPage } from "./LoanPage";
import { parseRoute, userFrom } from "./route";

function useHash(): string {
  const [hash, setHash] = useState(window.location.hash);
  // Stryker disable ArrayDeclaration: any constant list subscribes once, as [] does
  useEffect(() => {
    const follow = () => setHash(window.location.hash);
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);
  // Stryker restore ArrayDeclaration
  return hash;
}

export function ChooseUser({ onChoose }: { onChoose: (user: string) => void }) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = new FormData(event.currentTarget).get("user");
    if (typeof name === "string" && name.trim()) onChoose(name.trim());
  };
  return (
    <main className="page" data-testid="choose-user">
      <h1>Who is reading?</h1>
      <form onSubmit={submit} className="choose">
        <label htmlFor="user">User name</label>
        <input id="user" name="user" autoComplete="username" required />
        <button type="submit">Open the dashboard</button>
      </form>
    </main>
  );
}

export function Page({ hash, user }: { hash: string; user: string }) {
  const route = parseRoute(hash);
  switch (route.page) {
    case "book":
      return <BookPage user={user} month={route.month} />;
    case "loan":
      return <LoanPage user={user} deal={route.deal} loan={route.loan} month={route.month} />;
    default:
      return (
        <main className="page" data-testid="missing">
          <h1>No such page</h1>
          <p>
            Nothing lives at {route.hash}. <a href="#/book">Open the book's alerts</a>.
          </p>
        </main>
      );
  }
}

export function App() {
  const hash = useHash();
  const [user, setUser] = useState(() => userFrom(window.location.search));
  const choose = (name: string) => {
    const query = new URLSearchParams({ user: name });
    // Stryker disable next-line StringLiteral: browsers ignore replaceState's title argument
    window.history.replaceState(null, "", `?${query.toString()}${window.location.hash}`);
    setUser(name);
  };
  return (
    <>
      <header className="masthead">
        <a className="brand" href="#/book">
          CMBS surveillance
        </a>
        {user && <span className="reader">Reading as {user}</span>}
      </header>
      {user ? <Page hash={hash} user={user} /> : <ChooseUser onChoose={choose} />}
    </>
  );
}
