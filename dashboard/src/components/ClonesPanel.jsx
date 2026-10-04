export default function ClonesPanel({ clones }) {
  if (!clones) {
    return <p className="muted">TraceWeaver redundant-function analysis unavailable</p>;
  }
  const clusters = clones.clusters || [];
  return (
    <>
      <p className="muted">
        TraceWeaver savings if each cluster is reduced to one copy: <b>{clones.total_savings ?? 0}</b> debt-tokens.
        {" "}{clones.formula}
      </p>
      {clusters.length === 0 ? (
        <p className="muted">No redundant functions.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>names</th>
              <th>paths</th>
              <th>similarity</th>
              <th>token savings</th>
              <th>why</th>
            </tr>
          </thead>
          <tbody>
            {clusters.map((cluster) => {
              const members = cluster.members || [];
              return (
                <tr key={cluster.id}>
                  <td>{members.map((member) => member.name).join(", ")}</td>
                  <td>{members.map((member) => member.path).join(", ")}</td>
                  <td>{cluster.match} · {cluster.similarity}</td>
                  <td><b>{cluster.savings}</b></td>
                  <td>{cluster.note}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
