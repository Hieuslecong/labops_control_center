# Rollback

Disable the public resource in Pangolin. Run sudo bash infrastructure/pangolin/rollback.sh to stop and disable the dedicated connector. Revoke site credentials in Pangolin and revert the approved DNS change if needed. Restore VPS state from the backed-up configuration and certificates if the VPS installation is rolled back.
