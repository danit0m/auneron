import { FileUp } from "lucide-react";

import { Header } from "../components/layout/Header";

export function Upload() {
  return (
    <div className="page">
      <Header
        title="Importar CSV"
        subtitle="Importe clientes e registros financeiros"
      />

      <section className="page-content">
        <div className="placeholder-page">
          <FileUp size={42} />

          <h2>Importação de arquivo CSV</h2>

          <p>
            Nesta tela criaremos o envio do arquivo, o resumo da importação e a
            apresentação dos possíveis erros.
          </p>
        </div>
      </section>
    </div>
  );
}