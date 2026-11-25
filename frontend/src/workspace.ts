import { useSearchParams } from "react-router";

/** The workspace chosen in the header: `null` means all workspaces. Kept in the URL. */
export function useWorkspace(): [string | null, (name: string | null) => void] {
  const [params, setParams] = useSearchParams();
  const name = params.get("workspace");
  const set = (next: string | null) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next) p.set("workspace", next);
        else p.delete("workspace");
        return p;
      },
      { replace: true },
    );
  return [name, set];
}
