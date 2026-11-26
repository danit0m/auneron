import { Outlet } from "react-router-dom";

import { Sidebar } from "./Sidebar";

export function Layout() {
  return (
    <div className="app-layout">
      <Sidebar />

      <button
        type="button"
        className="menu-lateral-fundo"
        aria-label="Fechar menu"
        tabIndex={-1}
        onClick={() => window.dispatchEvent(new Event("auneron:fechar-menu"))}
      />

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}