import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { Button } from "../components/ui/controls";
import { useNavigationBlocker } from "../navigation/NavigationGuard";
/** Fixture for a retained draft whose handoff intentionally clears its blocker. */
export function NavigationHandoff() {
  const [dirty, setDirty] = useState(true),
    [handoff, setHandoff] = useState(false),
    navigate = useNavigate();
  useNavigationBlocker("handoff-probe", dirty, "Keep this fixture draft.");
  useEffect(() => {
    if (handoff) void navigate("/__foundation/public");
  }, [handoff, navigate]);
  return (
    <main className="sh-container">
      <h1>Draft handoff</h1>
      <Link to="/__foundation/public">Leave unsaved</Link>
      <Button
        onClick={() => {
          setDirty(false);
          setHandoff(true);
        }}
      >
        Retain draft and continue
      </Button>
    </main>
  );
}
