interface TabsProps {
  tabs: { id: string; label: string }[];
  active: string;
  onSelect: (id: string) => void;
}

export function Tabs({ tabs, active, onSelect }: TabsProps) {
  return (
    <nav className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={active === tab.id}
          className={active === tab.id ? "tab active" : "tab"}
          onClick={() => onSelect(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
