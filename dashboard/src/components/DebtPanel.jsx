const FORMULA_KEYS = ["tangled_cost", "choke_cost", "complex_cost", "hub_cost", "orphan_cost", "redundant_cost", "total"];

export default function DebtPanel({ debt }) {
  if (!debt || debt.total_debt == null) {
    return <p className="muted">debt analysis unavailable</p>;
  }
  const formula = debt.formula || {};
  const rules = [formula.tangled_rule, formula.choke_rule, formula.complex_bar, formula.hub_rule, formula.orphan_rule, formula.redundant_rule, formula.dollar_rate]
    .filter(Boolean);
  const example = (debt.worked_example || {}).arithmetic || [];
  const exampleIds = (debt.worked_example || {}).graph_node_ids || [];
  const findings = debt.findings || [];
  const shown = findings.slice(0, 20);

  return (
    <>
      <p className="muted">{debt.disclaimer || "Simulated local analysis of the indexed call graph."}</p>
      <div className="debt-head">
        <div className="metric">
          <label>total debt</label>
          <b>{debt.total_debt}</b>
          <span className="muted">debt-tokens</span>
        </div>
        <div className="metric">
          <label>dollar equivalent</label>
          <b>${debt.dollar_equivalent}</b>
          <span className="muted">{formula.dollar_rate || ""}</span>
        </div>
        {debt.redundant && (
          <div className="metric">
            <label>redundant functions</label>
            <b>{debt.redundant.cost}</b>
            <span className="muted">debt-tokens, beside choke / tangled / complex</span>
          </div>
        )}
      </div>
      <h3>Formula</h3>
      <div className="debt-formula">
        {FORMULA_KEYS.filter((key) => formula[key]).map((key) => (
          <div key={key}><code>{key}</code> = {formula[key]}</div>
        ))}
      </div>
      <ul>
        {rules.map((rule) => <li key={rule}>{rule}</li>)}
      </ul>
      <h3>for_renewal / retry-cycle</h3>
      <p className="muted">
        Measured from the indexed call graph. Graph node ids:{" "}
        <span className="debt-ids">{exampleIds.length ? exampleIds.join(", ") : "—"}</span>
      </p>
      <ol>
        {example.length ? example.map((line) => <li key={line}>{line}</li>) : <li>—</li>}
      </ol>
      <h3>Ranked findings</h3>
      <p className="muted">Graph node ids are the same ids the code graph renders. Highlighted nodes use these ids.</p>
      <table>
        <thead>
          <tr>
            <th>kind</th>
            <th>region</th>
            <th>why</th>
            <th>cost</th>
            <th>graph node id</th>
          </tr>
        </thead>
        <tbody>
          {shown.length === 0 && (
            <tr><td colSpan={5}>No findings.</td></tr>
          )}
          {shown.map((item) => (
            <tr key={item.id || item.name}>
              <td><span className={"debt-kind debt-" + item.kind}>{item.kind}</span></td>
              <td>
                {item.name}
                <div className="muted">{(item.regions || []).join("; ")}</div>
              </td>
              <td>{item.why}</td>
              <td>
                <b>{item.cost}</b>
                <div className="muted">{(item.breakdown || {}).arithmetic}</div>
              </td>
              <td className="debt-ids">{(item.graph_node_ids || []).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {findings.length > shown.length && (
        <p className="muted">Showing {shown.length} of {findings.length} findings. The total includes every finding.</p>
      )}
    </>
  );
}
