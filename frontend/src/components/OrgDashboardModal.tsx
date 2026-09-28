/**
 * Wave 0: 组织看板独立入口 —— 从设置页 tab 搬出，自带 Modal。
 */
import Modal from "./Modal";
import OrganizationDashboard from "./OrganizationDashboard";
import { useStore } from "../store";

export default function OrgDashboardModal() {
  const orgOpen = useStore((s) => s.orgOpen);
  const toggleOrg = useStore((s) => s.toggleOrg);
  return (
    <Modal title="组织看板 · Organization" open={orgOpen} onClose={toggleOrg} testId="modal-org">
      <OrganizationDashboard />
    </Modal>
  );
}
